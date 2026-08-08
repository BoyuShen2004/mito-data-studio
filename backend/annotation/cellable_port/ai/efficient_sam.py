"""EfficientSAM (ONNX) point/box-prompt mask prediction.

Ported from ``cellable/labelme/ai/efficient_sam.py`` (the ``EfficientSam``
class + its module-level ``_compute_mask_from_points``/``_compute_mask_from_box``
helpers). Kept: the encoder/decoder ONNX inference itself, the small-object
cleanup (``skimage.morphology.remove_small_objects``), and an image-embedding
cache (an embedding only depends on the image, not the prompt, so re-clicking
several points on the same slice re-uses it) — now backed by both an
in-process LRU (keyed by the slice's identity, see ``_embed``) and an
optional on-disk cache (see
``embed_cache.py``, ported from Cellable's
``utils/pre_compute_tiff_sam_feature.py`` idea).

Dropped vs. the original: the background ``threading.Thread`` that
pre-computed embeddings off the Qt event loop (mito's warm-up is a
lightweight, explicit "warm-embedding" request instead — see
``services.warm_ai_embedding`` — since a Django request/response cycle has
no equivalent to a long-lived Qt background thread to hand work off to).

**Thread-count fix (not a Cellable mechanism — Cellable runs on one local
desktop, this app runs on a shared HPC node):** ``onnxruntime`` defaults to
sizing its intra-op thread pool from the *physical* core count, which on a
SLURM allocation restricted to fewer CPUs (``-c 4``, cgroup-limited)
produces a flood of harmless-but-noisy ``pthread_setaffinity_np failed ...
Invalid argument`` messages — onnxruntime tries to pin threads to CPUs
outside the cgroup's affinity mask. ``_resolve_thread_count`` reads the
actual usable CPU count (SLURM's own env var first, then the process's real
affinity mask, then the OS-reported count) and both sessions are built with
an explicit ``SessionOptions`` instead of onnxruntime's own guess.
"""

from __future__ import annotations

import collections
import hashlib
import logging
import os
import threading
import time

import numpy as np
from django.conf import settings

_timing_log = logging.getLogger("mito.ai.timing")
_runtime_log = logging.getLogger(__name__)


def _timing_enabled() -> bool:
    try:
        from django.conf import settings

        return bool(getattr(settings, "MITO_AI_TIMING", False))
    except Exception:
        return False


_MAX_EMBEDDING_CACHE = 16
# Encoder + decoder are run one after another for a single request, never
# concurrently within one predict call — inter-op parallelism (independent
# graph branches running at once) buys nothing here and only adds thread-pool
# overhead, so it's pinned to 1 regardless of the intra-op count below.
_MAX_INTRA_OP_THREADS = 16


def _resolve_thread_count() -> int:
    """Usable CPU count for this process, capped sensibly.

    Priority: ``SLURM_CPUS_PER_TASK`` (what the scheduler actually granted,
    when running under `sbatch`/`srun`) -> ``os.sched_getaffinity(0)`` (the
    real cgroup-restricted affinity mask on Linux — more reliable than
    ``os.cpu_count()``, which reports the *node's* total CPUs regardless of
    what this process is actually allowed to use) -> ``os.cpu_count()`` (last
    resort, e.g. non-Linux or affinity unavailable) -> ``1``.
    """
    raw = os.environ.get("SLURM_CPUS_PER_TASK")
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return min(n, _MAX_INTRA_OP_THREADS)
        except ValueError:
            pass
    sched_getaffinity = getattr(os, "sched_getaffinity", None)
    if sched_getaffinity is not None:
        try:
            n = len(sched_getaffinity(0))
            if n > 0:
                return min(n, _MAX_INTRA_OP_THREADS)
        except OSError:
            pass
    n = os.cpu_count() or 1
    return min(n, _MAX_INTRA_OP_THREADS)


def _session_options():
    import onnxruntime

    threads = _resolve_thread_count()
    opts = onnxruntime.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    return opts


_cuda_preload_done = False
_cuda_preload_lock = threading.Lock()


def _preload_cuda_libs() -> None:
    global _cuda_preload_done
    with _cuda_preload_lock:
        if _cuda_preload_done:
            return
        _cuda_preload_done = True
        try:
            import onnxruntime

            preload = getattr(onnxruntime, "preload_dlls", None)
            if preload is not None:
                preload()
        except Exception:
            _timing_log.debug("CUDA preload failed", exc_info=True)


def _visible_gpu_count() -> int:
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    if visible is not None:
        return len([item for item in visible.split(",") if item.strip()])
    try:
        return len(os.listdir("/proc/driver/nvidia/gpus"))
    except OSError:
        return 0


def _cuda_device_id() -> int:
    configured = getattr(settings, "MITO_AI_CUDA_DEVICE", None)
    if configured not in (None, ""):
        try:
            return max(0, int(configured))
        except (TypeError, ValueError):
            pass
    count = _visible_gpu_count()
    return 0 if count <= 1 else os.getpid() % count


def _make_session(model_path: str, opts, *, cuda: bool):
    import onnxruntime

    if cuda and bool(getattr(settings, "MITO_AI_ONNX_CUDA", True)):
        _preload_cuda_libs()
        try:
            session = onnxruntime.InferenceSession(
                model_path,
                sess_options=opts,
                providers=[
                    ("CUDAExecutionProvider", {"device_id": _cuda_device_id()}),
                    "CPUExecutionProvider",
                ],
            )
            if "CUDAExecutionProvider" in session.get_providers():
                _runtime_log.info(
                    "EfficientSAM ONNX session model=%s requested_cuda=true "
                    "device_id=%d providers=%s",
                    os.path.basename(model_path),
                    _cuda_device_id(),
                    session.get_providers(),
                )
                return session
        except Exception:
            _timing_log.warning("CUDA session failed for %s", model_path, exc_info=True)
    session = onnxruntime.InferenceSession(
        model_path, sess_options=opts, providers=["CPUExecutionProvider"]
    )
    _runtime_log.warning(
        "EfficientSAM ONNX session model=%s requested_cuda=%s configured_cuda=%s "
        "device_id=%d available_providers=%s attached_providers=%s",
        os.path.basename(model_path),
        cuda,
        bool(getattr(settings, "MITO_AI_ONNX_CUDA", True)),
        _cuda_device_id(),
        onnxruntime.get_available_providers(),
        session.get_providers(),
    )
    return session


def _to_rgb(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return np.stack([image] * 3, axis=-1).astype(np.uint8)
    if image.ndim == 3 and image.shape[2] in (3, 4):
        return image[:, :, :3].astype(np.uint8)
    raise ValueError(f"Unsupported image shape {image.shape}. Must be 2D or 3D (H, W, C).")


class EfficientSam:
    def __init__(self, encoder_path: str, decoder_path: str):
        opts = _session_options()
        self._encoder = _make_session(encoder_path, opts, cuda=True)
        self._decoder = _make_session(
            decoder_path, _session_options(), cuda=True
        )
        self._lock = threading.Lock()
        self._embedding_cache: "collections.OrderedDict[bytes, np.ndarray]" = (
            collections.OrderedDict()
        )
        _runtime_log.info(
            "EfficientSAM runtime configured_cuda=%s device_id=%d encoder_providers=%s "
            "decoder_providers=%s threads=%d",
            bool(getattr(settings, "MITO_AI_ONNX_CUDA", True)),
            _cuda_device_id(),
            self._encoder.get_providers(),
            self._decoder.get_providers(),
            _resolve_thread_count(),
        )

    def _embed(self, image: np.ndarray, disk_path=None) -> np.ndarray:
        """Encoder embedding for ``image``, cached in-process and, if
        ``disk_path`` is given, on disk too (see ``embed_cache.py``). A disk
        hit still gets folded into the in-process LRU so a second click on the
        same slice this session never re-reads the file.

        **Cache key**: ``disk_path`` when there is one. It already identifies
        (volume, axis, index, model variant, image mtime) exactly — i.e. it
        identifies the image — so keying on it avoids hashing the image
        itself. Hashing was not free: an EM slice is tens of MB, and Point
        Mask's cursor-follow predicts run this on *every* mouse move, paying a
        full copy + digest of the slice per call just to look up a cache it
        was about to hit. Callers with no disk path (unit tests, ad-hoc use)
        still get a content hash — over a buffer view, without copying.

        The RGB conversion is likewise deferred until an encode is actually
        needed: it triples a large slice in memory and the decoder only ever
        needs the image's *shape*.
        """
        timed = _timing_enabled()
        t0 = time.perf_counter() if timed else 0.0

        def _log(source: str) -> None:
            if timed:
                _timing_log.info(
                    "embed source=%s %.1fms disk=%s",
                    source,
                    (time.perf_counter() - t0) * 1000.0,
                    getattr(disk_path, "name", None),
                )

        if disk_path is not None:
            key = f"path:{disk_path}".encode()
        else:
            key = hashlib.blake2b(np.ascontiguousarray(image), digest_size=16).digest()
        with self._lock:
            cached = self._embedding_cache.get(key)
            if cached is not None:
                self._embedding_cache.move_to_end(key)
                _log("inproc")
                return cached

        if disk_path is not None:
            from . import embed_cache

            cached = embed_cache.load(disk_path)
            if cached is not None:
                self._store_embedding(key, cached)
                _log("disk")
                return cached

        def encode():
            image_rgb = _to_rgb(image)
            batched = image_rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
            (value,) = self._encoder.run(
                output_names=None, input_feed={"batched_images": batched}
            )
            return value

        if disk_path is not None:
            from . import embed_cache

            with embed_cache.exclusive_compute_lock(disk_path):
                cached = embed_cache.load(disk_path)
                if cached is not None:
                    self._store_embedding(key, cached)
                    _log("disk-after-wait")
                    return cached
                embedding = encode()
                embed_cache.save(disk_path, embedding)
        else:
            embedding = encode()
        self._store_embedding(key, embedding)
        _log("encoder")
        return embedding

    def _store_embedding(self, key: bytes, embedding: np.ndarray) -> None:
        with self._lock:
            self._embedding_cache[key] = embedding
            self._embedding_cache.move_to_end(key)
            while len(self._embedding_cache) > _MAX_EMBEDDING_CACHE:
                self._embedding_cache.popitem(last=False)

    def warm(self, image: np.ndarray, disk_path=None) -> None:
        """Compute (and cache, in-process + optionally on disk) the
        embedding for ``image`` without predicting anything — so the first
        real click on this slice only has to run the (fast) decoder. Mirrors
        the *intent* of Cellable's background embedding thread; see
        ``services.warm_ai_embedding`` for how it's triggered."""
        self._embed(image, disk_path=disk_path)

    def predict_mask_from_points(
        self, image: np.ndarray, points, point_labels, disk_path=None
    ) -> np.ndarray:
        """``points``: ``[[x, y], ...]``; ``point_labels``: ``1`` (positive) /
        ``0`` (negative) per point, same convention as Cellable's ai_mask
        mode (shift+click = negative)."""
        embedding = self._embed(image, disk_path=disk_path)
        return _decode_mask(self._decoder, image, embedding, points, point_labels)

    def predict_mask_from_box(self, image: np.ndarray, box_points, disk_path=None) -> np.ndarray:
        """``box_points``: ``[[x1, y1], [x2, y2]]``. SAM's box-prompt point
        labels are the fixed pair ``[2, 3]`` (top-left/bottom-right corner
        markers), same as Cellable's ``_compute_mask_from_box``."""
        embedding = self._embed(image, disk_path=disk_path)
        return _decode_mask(self._decoder, image, embedding, box_points, [2, 3])


def _decode_mask(decoder, image, embedding, points, point_labels) -> np.ndarray:
    import skimage.morphology

    timed = _timing_enabled()
    t0 = time.perf_counter() if timed else 0.0

    input_point = np.array(points, dtype=np.float32)[None, None, :, :]
    input_label = np.array(point_labels, dtype=np.float32)[None, None, :]
    masks, ious, _ = decoder.run(
        None,
        {
            "image_embeddings": embedding,
            "batched_point_coords": input_point,
            "batched_point_labels": input_label,
            "orig_im_size": np.array(image.shape[:2], dtype=np.int64),
        },
    )
    iou_vector = np.asarray(ious)[0, 0]
    best = int(np.argmax(iou_vector)) if iou_vector.size else 0
    mask = np.asarray(masks)[0, 0, best, :, :] > 0.0
    if mask.any():
        # `max_size` replaces the deprecated `min_size` as of skimage 0.26
        # (see that release's FutureWarning) — the two aren't quite the same
        # comparison (old: strictly smaller than min_size is removed; new:
        # smaller-than-**or-equal-to** max_size is removed), but since the
        # threshold here is a float percentage of a runtime-computed mask
        # sum rather than a fixed integer, the boundary case where that
        # off-by-one-pixel difference would actually matter essentially
        # never occurs — same ~5% small-object cleanup intent as Cellable,
        # just with the current (non-deprecated) keyword.
        skimage.morphology.remove_small_objects(mask, max_size=mask.sum() * 0.05, out=mask)
    if timed:
        _timing_log.info("decode %.1fms", (time.perf_counter() - t0) * 1000.0)
    return mask
