"""SAM 2 video-predictor wrapper — a self-contained port of
``MTS/mts_mask_editor/core/sam2_wrapper.py``.

The original lived in a sibling codebase (``MTS``) and hardcoded
``SAM2_ROOT = Path("/projects/weilab/shenb/MTS/sam2")``, so this app's own
``Sam2TrackingProvider`` (``sam2.py`` in this package) could only ever run
against a checkout that happened to exist outside this repo. This is a copy
with that one change: ``sam2_root`` is a constructor argument instead of a
module constant, sourced from ``settings.MITO_SAM2_ROOT`` by the caller,
which by default now points at ``vendor/sam2/`` — a full copy of
facebookresearch/sam2 (code + downloaded checkpoints) living under this
repo's own root. See the root ``README.md`` for provenance and
``docs/development.md``
for the full setup story.

Everything else (the video-propagation API surface, point/box/mask prompt
handling, bfloat16-on-Ampere-or-newer autocast) is unchanged from the MTS
original. ``torch``/``sam2`` are imported lazily inside ``__init__`` (via the
``sys.path`` insertion below), same as before, so importing this *module*
never requires either — only actually instantiating ``SAM2Wrapper`` does,
which only happens from ``Sam2TrackingProvider._load()`` on a GPU node.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import threading
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Literal

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

PropagationDirection = Literal["forward", "backward", "both"]

DEFAULT_CHECKPOINT_NAME = "checkpoints/sam2.1_hiera_large.pt"
DEFAULT_CONFIG = "configs/sam2.1/sam2.1_hiera_l.yaml"

# How many slices' encoder features to keep on the GPU. Scrubbing is the
# motivating case: an annotator steps z back and forth over a handful of
# planes, and re-encoding one costs ~240 ms while replaying cached features
# costs nothing. Eight slots covers the working set of a scrub without
# meaningfully denting a 2080 Ti — see `image_cache_bytes` for the real
# number, measured at 16 MiB per slot here (the encoder's output is a fixed
# size, so this does not grow with the plane).
DEFAULT_IMAGE_CACHE_SLOTS = 8


def _setting_int(name: str, default: int) -> int:
    """Read a Django setting without making this module import Django."""
    try:
        from django.conf import settings

        return int(getattr(settings, name, default))
    except Exception:
        return default


class SAM2Wrapper:
    """Expose a small API for SAM 2 video propagation over z-slices."""

    def __init__(
        self,
        sam2_root: Path | str,
        checkpoint: Path | str | None = None,
        config: str | None = None,
        device: str | None = None,
    ) -> None:
        self.sam2_root = Path(sam2_root)
        if str(self.sam2_root) not in sys.path:
            sys.path.insert(0, str(self.sam2_root))

        try:
            import torch  # noqa: PLC0415 — deliberately lazy, see module docstring
        except ImportError as exc:
            raise RuntimeError(
                "SAM2 tracking needs torch. Install with: "
                "conda env update -f environment.yml --prune"
            ) from exc

        from sam2.build_sam import build_sam2_video_predictor

        self.checkpoint = Path(checkpoint) if checkpoint else self.sam2_root / DEFAULT_CHECKPOINT_NAME
        self.config = config or DEFAULT_CONFIG
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device

        if not self.checkpoint.exists():
            raise FileNotFoundError(
                f"SAM 2 checkpoint not found: {self.checkpoint}. Set "
                "MITO_SAM2_CHECKPOINT, or download "
                "sam2.1_hiera_large.pt into "
                f"{self.sam2_root / 'checkpoints'} "
                "(see root README.md)."
            )

        logger.info("Loading SAM 2 video predictor from %s on %s", self.checkpoint, self.device)
        if self.device.startswith("cuda"):
            if torch.cuda.get_device_properties(self.device).major >= 8:
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
        self.predictor = build_sam2_video_predictor(
            config_file=self.config,
            ckpt_path=str(self.checkpoint),
            device=self.device,
            # Skip CUDA connected-components postprocess — vendor/sam2 no
            # longer ships setup.py/csrc; core tracking works without it.
            apply_postprocessing=False,
        )
        self._autocast_dtype = (
            (torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16)
            if self.device.startswith("cuda") and torch.cuda.is_available()
            else None
        )
        self.inference_state = None
        self._temp_dir: tempfile.TemporaryDirectory | None = None
        self._image_stack: np.ndarray | None = None
        self._image_predictor = None
        # LRU of image-encoder output, keyed by slice identity — see
        # `_ensure_image_features`. Ordered newest-last.
        self._image_cache: OrderedDict[str, dict] = OrderedDict()
        self._image_cache_slots = max(
            1, _setting_int("MITO_SAM2_IMAGE_CACHE_SLOTS", DEFAULT_IMAGE_CACHE_SLOTS)
        )
        self._image_cache_stats = {
            "hit": 0, "disk_hit": 0, "miss": 0, "uncached": 0, "evicted": 0
        }
        # Optional L2 behind the LRU, injected by the Annotate layer (see
        # `cellable_port/ai/sam2_feature_cache`) so this module keeps knowing
        # only about SAM 2 and nothing about the data root.
        self._feature_store = None
        # Guards the image predictor's single feature slot, which only one
        # caller uses (Annotate's mask tools — Track prompts the *video*
        # predictor instead). Held for one encode/decode, never a propagation,
        # so an Annotate click can never queue behind Track's long lock.
        self._image_lock = threading.RLock()

    @contextmanager
    def _inference_context(self) -> Iterator[None]:
        """Run CUDA work under autocast — **never** bare fp32.

        SAM 2 stores its memory bank in bfloat16 unconditionally
        (``sam2_video_predictor.py`` casts ``maskmem_features.to(torch.bfloat16)``
        in both ``_run_single_frame_inference`` and ``_run_memory_encoder``),
        while the module weights stay fp32. Only autocast reconciles the two:
        it casts *both* operands of each matmul to the autocast dtype.

        This used to be gated on ``compute capability >= 8`` ("V100 stays
        fp32"), which meant that on this deployment's sm_75 (RTX 2080 Ti)
        nodes memory attention ran with bf16 memory against fp32 weights and
        died with ``mat1 and mat2 must have the same dtype, but got BFloat16
        and Float``. It only surfaced once a parent had **two or more** child
        classes, because the multi-object consolidation path
        (``_run_memory_encoder``) is what first pushes bf16 memory through
        ``memory_attention`` — so single-seed Track looked fine while every
        real fork-tracking propagate failed.

        bf16 is preferred (SAM 2's native memory dtype, no range loss); fp16
        is the fallback for the rare CUDA device without bf16 support, and is
        still correct because autocast casts the bf16 memory down with it.
        """
        import torch

        if self._autocast_dtype is not None:
            with torch.inference_mode(), torch.autocast(
                "cuda", dtype=self._autocast_dtype
            ):
                yield
        else:
            with torch.inference_mode():
                yield

    def initialize_sequence(self, image_stack: np.ndarray) -> None:
        """Treat Z slices as video frames. Grayscale stacks are exported as RGB JPEGs."""
        self.reset_session()
        self._image_stack = np.asarray(image_stack)
        if self._image_stack.ndim != 3:
            raise ValueError("image_stack must be 3D (Z, Y, X)")

        self._temp_dir = tempfile.TemporaryDirectory(prefix="mito_sam2_")
        temp_path = Path(self._temp_dir.name)
        logger.info(
            "Exporting %d slice(s) to temporary JPEG folder: %s",
            self._image_stack.shape[0],
            temp_path,
        )
        self._export_stack_as_jpegs(self._image_stack, temp_path)

        with self._inference_context():
            self.inference_state = self.predictor.init_state(
                video_path=str(temp_path),
                offload_video_to_cpu=(self.device == "cpu"),
            )
        logger.info(
            "SAM 2 sequence initialized: %d frames, %dx%d",
            self.inference_state["num_frames"],
            self.inference_state["video_height"],
            self.inference_state["video_width"],
        )

    def reset_session(self) -> None:
        """Drop inference state and temp frames."""
        self.inference_state = None
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None

    @staticmethod
    def _slice_to_rgb(slice_2d: np.ndarray) -> np.ndarray:
        sl = np.asarray(slice_2d, dtype=np.float32)
        lo, hi = np.percentile(sl, (1, 99))
        if hi <= lo:
            lo, hi = float(sl.min()), float(sl.max())
        if hi <= lo:
            u8 = np.zeros(sl.shape, dtype=np.uint8)
        else:
            u8 = np.clip((sl - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)
        return np.stack([u8, u8, u8], axis=-1)

    @staticmethod
    def _export_worker_count(frames: int) -> int:
        """How many threads to encode the frame stack with.

        Bounded by the frames themselves, by an env-tunable ceiling, and by the
        CPUs this process may actually use — ``sched_getaffinity`` rather than
        ``cpu_count`` so a cgroup-restricted service does not size a pool from
        the whole node (the same distinction the ported ``_resolve_thread_
        count`` documents). Kept modest because up to three gunicorn workers can
        be in here at once.
        """
        import os

        ceiling = 8
        raw = os.environ.get("MITO_SAM2_EXPORT_THREADS")
        if raw:
            try:
                ceiling = max(1, int(raw))
            except ValueError:
                pass
        sched_getaffinity = getattr(os, "sched_getaffinity", None)
        if sched_getaffinity is not None:
            try:
                ceiling = min(ceiling, max(1, len(sched_getaffinity(0))))
            except OSError:
                pass
        else:
            ceiling = min(ceiling, max(1, os.cpu_count() or 1))
        return max(1, min(ceiling, int(frames)))

    @staticmethod
    def _export_stack_as_jpegs(stack: np.ndarray, out_dir: Path) -> None:
        """Write each z-slice as an RGB JPEG for SAM 2's video loader.

        Encoded in parallel. This runs on **every** propagate — twice when the
        XY border-expansion retry fires — and it was the single largest fixed
        cost in a Track request: measured 12.3 s to export a 64x2048x2048 crop
        serially, versus 1.7 s across 8 threads (2.7 s -> 0.33 s at
        64x1024x1024). Threads, not processes, because the two expensive steps
        both drop the GIL (``np.percentile``'s partition and Pillow's JPEG
        encoder) and the stack would otherwise have to be pickled to children.

        Each task writes its own distinct filename into a private temp dir, so
        there is nothing shared to synchronise.
        """
        from concurrent.futures import ThreadPoolExecutor

        out_dir.mkdir(parents=True, exist_ok=True)
        frames = int(stack.shape[0])

        def export(z: int) -> None:
            rgb = SAM2Wrapper._slice_to_rgb(stack[z])
            Image.fromarray(rgb).save(out_dir / f"{z:05d}.jpg", quality=95)

        workers = SAM2Wrapper._export_worker_count(frames)
        if workers == 1:
            for z in range(frames):
                export(z)
            return
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # list() so an exception in any frame propagates instead of being
            # swallowed by the lazy map — a half-exported stack must not reach
            # init_state as if it were complete.
            list(pool.map(export, range(frames)))

    def _get_image_predictor(self):
        """The image predictor, built on the **already-loaded** video model.

        `SAM2VideoPredictor` is a `SAM2Base`, and `SAM2ImagePredictor` only
        reads its argument — every piece of mutable state it owns
        (`_features`, `_is_image_set`, `_orig_hw`) lives on the predictor,
        while tracking's `inference_state` is passed around as an argument
        and never stored on the model. So the two predictors can share one
        set of weights.

        That matters on this deployment: `build_sam2` here would load a
        second ~2 GB copy of hiera_large per gunicorn worker, and the
        production GPU already sits at 9.2 of 11.3 GB with three workers
        holding the video model. Sharing makes the image path cost nothing
        extra whenever tracking is loaded, which it always is here.
        """
        if self._image_predictor is None:
            from sam2.sam2_image_predictor import SAM2ImagePredictor

            self._image_predictor = SAM2ImagePredictor(self.predictor)
        return self._image_predictor

    def predict_single_frame(
        self,
        slice_2d: np.ndarray,
        *,
        points: list[tuple[int, int]] | None = None,
        point_labels: list[int] | None = None,
        box: tuple[int, int, int, int] | None = None,
        cache_key: str | None = None,
        candidates: bool = False,
    ) -> np.ndarray:
        """Fast single-slice SAM 2 (image model only — no full-stack JPEG export).

        ``cache_key`` identifies the *image* being prompted. Interactive mask
        editing prompts the same slice over and over — click, refine, negative
        click — and `set_image` (the image encoder) is ~95% of the cost of a
        call, while the prompt decoder that follows it is a few milliseconds.
        Passing a stable key lets a repeat prompt replay the encoder output
        from the LRU in `_ensure_image_features` instead of recomputing it,
        which is what makes scrubbing back to a recent slice free rather than
        a fresh quarter-second encode.

        ``candidates=True`` returns ``(masks, ious)`` — every mask SAM 2
        produced with the model's own predicted IoU for each — instead of
        collapsing to the highest-scoring one here. A single point asks for
        `multimask_output`, and on dense tissue the three answers are not
        interchangeable: one of them is routinely a near-full-frame blanket
        over the whole field. Picking between them needs to know where the
        click was, which this layer does not, so Annotate does it in
        ``cellable_port/ai/sam2_masks``. Tracking keeps the default.
        """
        if box is None and not points:
            raise ValueError("predict_single_frame requires box and/or points")

        predictor = self._get_image_predictor()

        # Deliberately *not* under `_inference_context()`, unlike every other
        # CUDA path here. That looks like an oversight and is not: measured on
        # this deployment, autocasting the image encoder runs 343 ms against
        # 229 ms in plain fp32 — the casts cost more than the reduced-precision
        # matmuls save at this one-image size, and the cached features come
        # out fp32 either way. The masks agree to IoU 0.996, so there is
        # nothing to buy here. Re-measure before "fixing" this.
        kwargs: dict = {"multimask_output": bool(points and len(points) == 1 and box is None)}
        if box is not None:
            x1, y1, x2, y2 = box
            kwargs["box"] = np.array([x1, y1, x2, y2], dtype=np.float32)
            kwargs["multimask_output"] = False
        if points:
            kwargs["point_coords"] = np.array(points, dtype=np.float32)
            kwargs["point_labels"] = np.array(
                point_labels or [1] * len(points),
                dtype=np.int32,
            )

        # One lock across encode *and* decode: `predict` reads the feature slot
        # that `_ensure_image_features` just filled, so releasing in between
        # would let a second caller overwrite it mid-prompt.
        with self._image_lock:
            self._ensure_image_features(predictor, slice_2d, cache_key)
            masks, ious, _ = predictor.predict(normalize_coords=True, **kwargs)
        masks = np.asarray(masks, dtype=bool)
        if masks.ndim == 2:
            masks = masks[None]
        ious = np.atleast_1d(np.asarray(ious, dtype=float))
        if candidates:
            return list(masks), ious
        if len(masks) > 1:
            return masks[int(np.argmax(ious))]
        return masks[0]

    def set_feature_store(self, store) -> None:
        """Attach an L2 for encoder features (``load``/``save``/``compute_lock``)."""
        self._feature_store = store

    def is_slice_warm(self, cache_key: str) -> bool:
        """Whether prompting this slice would avoid the image encoder — in
        this worker's LRU, or on disk where any worker can reach it."""
        if cache_key is None:
            return False
        if cache_key in self._image_cache:
            return True
        store = self._feature_store
        return store is not None and store.has(cache_key)

    def warm_slice(self, slice_2d: np.ndarray, *, cache_key: str) -> str:
        """Encode a slice *opportunistically*, for the viewer's slice-open warm.

        Unlike `encode_slice`, this never waits. Warming is speculative work
        on behalf of a click that may not come, and the viewer fires three of
        them per slice change (the plane and its two neighbours); letting
        them block meant a warm sat on a gunicorn worker thread waiting for
        the GPU lock while the slice image and label requests the annotator
        is actually waiting for queued up behind it. Scrubbing felt slow
        *because* of the prefetch meant to speed it up.

        Skipping is close to free: the slice gets encoded on demand at click
        time, or by the next warm once the GPU is idle.
        """
        if self.is_slice_warm(cache_key):
            return "hit"
        if not self._image_lock.acquire(blocking=False):
            return "busy"
        try:
            return self._ensure_image_features(
                self._get_image_predictor(), slice_2d, cache_key
            )
        finally:
            self._image_lock.release()

    def encode_slice(self, slice_2d: np.ndarray, *, cache_key: str) -> str:
        """Fill the cache for a slice without prompting it.

        This is what "warm on slice open" should call: it pays the encoder
        cost off the click path and returns "hit" when the work was already
        done, so warming a slice the annotator scrubbed back to is free.
        """
        predictor = self._get_image_predictor()
        with self._image_lock:
            return self._ensure_image_features(predictor, slice_2d, cache_key)

    def _ensure_image_features(self, predictor, slice_2d, cache_key) -> str:
        """Put `cache_key`'s encoder features into the predictor's feature slot.

        `SAM2ImagePredictor` writes `_features` only in `set_image`, and
        `predict` merely reads it, so the output of an encode is a plain value
        that can be stashed and replayed. Restoring it skips `forward_image`,
        which is ~95% of the cost of a prompt on an already-seen slice.

        Returns "hit" / "miss" / "uncached", for timing and for tests.
        """
        if cache_key is None:
            # No stable identity for this image — encode it and do not let it
            # into the cache, where it could later be served for a different
            # slice that happens to arrive with no key either.
            predictor.set_image(self._slice_to_rgb(slice_2d))
            self._image_cache_stats["uncached"] += 1
            return "uncached"

        cached = self._image_cache.get(cache_key)
        if cached is not None:
            self._image_cache.move_to_end(cache_key)
            self._install(predictor, cached["features"], cached["orig_hw"])
            self._image_cache_stats["hit"] += 1
            return "hit"

        store = self._feature_store
        if store is None:
            predictor.set_image(self._slice_to_rgb(slice_2d))
            self._remember(cache_key, predictor._features, predictor._orig_hw)
            self._image_cache_stats["miss"] += 1
            return "miss"

        payload = store.load(cache_key)
        if payload is None:
            # Hold the cross-process lock across the encode so that the
            # viewer's three concurrent slice warms cost one encode between
            # them, not one each — then re-check, because the worker that
            # held the lock before us has just written the answer.
            with store.compute_lock(cache_key):
                payload = store.load(cache_key)
                if payload is None:
                    predictor.set_image(self._slice_to_rgb(slice_2d))
                    self._remember(cache_key, predictor._features, predictor._orig_hw)
                    store.save(cache_key, self._export(predictor._features, predictor._orig_hw))
                    self._image_cache_stats["miss"] += 1
                    return "miss"

        features, orig_hw = self._import(payload)
        self._install(predictor, features, orig_hw)
        self._remember(cache_key, features, orig_hw)
        self._image_cache_stats["disk_hit"] += 1
        return "disk_hit"

    @staticmethod
    def _install(predictor, features, orig_hw) -> None:
        predictor._features = features
        predictor._orig_hw = orig_hw
        predictor._is_image_set = True
        predictor._is_batch = False

    def _remember(self, cache_key, features, orig_hw) -> None:
        self._image_cache[cache_key] = {"features": features, "orig_hw": orig_hw}
        self._image_cache.move_to_end(cache_key)
        while len(self._image_cache) > self._image_cache_slots:
            # Dropping the entry drops the last reference to those GPU
            # tensors, unless the predictor still points at them, in which
            # case the next encode releases them.
            self._image_cache.popitem(last=False)
            self._image_cache_stats["evicted"] += 1

    @staticmethod
    def _export(features, orig_hw) -> dict:
        """GPU tensors -> float16 arrays for the L2. Half precision is exact
        for this purpose (see `sam2_feature_cache`) and halves both the file
        and the time to read it back."""
        return {
            "image_embed": features["image_embed"].detach().half().cpu().numpy(),
            "high_res_feats": [
                feat.detach().half().cpu().numpy() for feat in features["high_res_feats"]
            ],
            "orig_hw": orig_hw,
        }

    def _import(self, payload) -> tuple[dict, list]:
        import torch

        def restore(array):
            # Back to fp32 on the device: the decoder runs fp32, and casting
            # here keeps that the only place precision is decided.
            return torch.from_numpy(np.ascontiguousarray(array)).to(self.device).float()

        features = {
            "image_embed": restore(payload["image_embed"]),
            "high_res_feats": [restore(feat) for feat in payload["high_res_feats"]],
        }
        return features, [tuple(hw) for hw in payload["orig_hw"]]

    def image_cache_stats(self) -> dict:
        with self._image_lock:
            return {
                **self._image_cache_stats,
                "slots": self._image_cache_slots,
                "held": len(self._image_cache),
            }

    def image_cache_bytes(self) -> int:
        """GPU bytes currently held by cached features (0 for CPU tensors)."""
        total = 0
        with self._image_lock:
            for entry in self._image_cache.values():
                tensors = [entry["features"]["image_embed"]]
                tensors += list(entry["features"]["high_res_feats"])
                for t in tensors:
                    total += t.element_size() * t.nelement()
        return total

    def reset_image_cache(self) -> None:
        with self._image_lock:
            self._image_cache.clear()
            for key in self._image_cache_stats:
                self._image_cache_stats[key] = 0

    def _require_state(self) -> None:
        if self.inference_state is None:
            raise RuntimeError("Call initialize_sequence() before adding prompts.")

    def add_point_prompt(
        self,
        slice_index: int,
        obj_id: int,
        points: list[tuple[int, int]],
        labels: list[int],
        *,
        clear_old_points: bool = False,
    ) -> np.ndarray:
        """Register point prompts and return the predicted boolean mask."""
        return self.predict_frame_mask(
            slice_index,
            obj_id,
            points=points,
            point_labels=labels,
            clear_old_points=clear_old_points,
        )

    def add_box_prompt(
        self,
        slice_index: int,
        obj_id: int,
        box: tuple[int, int, int, int],
    ) -> np.ndarray:
        """Register box prompt (x1, y1, x2, y2) and return the predicted boolean mask."""
        return self.predict_frame_mask(
            slice_index,
            obj_id,
            box=box,
            clear_old_points=True,
        )

    def add_mask_prompt(
        self,
        slice_index: int,
        obj_id: int,
        mask: np.ndarray,
    ) -> np.ndarray:
        """Register a binary mask prompt for one object on one slice."""
        self._require_state()
        mask_bool = np.asarray(mask, dtype=bool)
        if mask_bool.ndim != 2:
            raise ValueError(f"mask must be 2D, got shape {mask_bool.shape}")
        with self._inference_context():
            _, obj_ids, masks = self.predictor.add_new_mask(
                inference_state=self.inference_state,
                frame_idx=int(slice_index),
                obj_id=int(obj_id),
                mask=mask_bool,
            )
        return self._mask_for_obj_id(obj_ids, masks, int(obj_id))

    def predict_frame_mask(
        self,
        slice_index: int,
        obj_id: int,
        *,
        points: list[tuple[int, int]] | None = None,
        point_labels: list[int] | None = None,
        box: tuple[int, int, int, int] | None = None,
        clear_old_points: bool = True,
    ) -> np.ndarray:
        """Run single-frame SAM 2 from point/box prompts and return a boolean HxW mask."""
        self._require_state()
        if box is None and not points:
            raise ValueError("predict_frame_mask requires box and/or points")

        kwargs: dict = {
            "inference_state": self.inference_state,
            "frame_idx": int(slice_index),
            "obj_id": int(obj_id),
            "clear_old_points": clear_old_points,
        }
        if box is not None:
            x1, y1, x2, y2 = box
            kwargs["box"] = np.array([x1, y1, x2, y2], dtype=np.float32)
        if points:
            kwargs["points"] = np.array(points, dtype=np.float32)
            kwargs["labels"] = np.array(point_labels or [1] * len(points), dtype=np.int32)

        with self._inference_context():
            _, obj_ids, masks = self.predictor.add_new_points_or_box(**kwargs)
        return self._mask_for_obj_id(obj_ids, masks, int(obj_id))

    @staticmethod
    def _mask_for_obj_id(obj_ids: list[int], masks, obj_id: int) -> np.ndarray:
        try:
            idx = list(int(i) for i in obj_ids).index(int(obj_id))
        except ValueError as exc:
            raise RuntimeError(f"Object id {obj_id} not in SAM output {obj_ids}") from exc
        return SAM2Wrapper._mask_tensor_to_numpy(masks, obj_index=idx)

    @staticmethod
    def _collect_frame_masks(obj_ids: list[int], masks) -> dict[int, np.ndarray]:
        out: dict[int, np.ndarray] = {}
        for i, oid in enumerate(obj_ids):
            out[int(oid)] = SAM2Wrapper._mask_tensor_to_numpy(masks, obj_index=i)
        return out

    def propagate_multi(
        self,
        start_slice: int,
        z_range: tuple[int, int],
        direction: PropagationDirection = "both",
        backward_start_slice: int | None = None,
    ) -> dict[int, dict[int, np.ndarray]]:
        """
        Propagate all registered objects within inclusive z_range.

        Returns {obj_id: {slice_index: binary_mask}}.
        """
        self._require_state()
        num_frames = self.inference_state["num_frames"]
        z_lo = max(0, min(int(z_range[0]), num_frames - 1))
        z_hi = max(z_lo, min(int(z_range[1]), num_frames - 1))
        logger.info("Multi-object propagate within z=%d..%d", z_lo, z_hi)

        fwd_start = max(z_lo, min(int(start_slice), z_hi))
        bwd_start = max(z_lo, min(int(backward_start_slice or start_slice), z_hi))

        forward: dict[int, dict[int, np.ndarray]] = {}
        backward: dict[int, dict[int, np.ndarray]] = {}

        with self._inference_context():
            if direction in ("forward", "both"):
                fwd_max = max(1, z_hi - fwd_start + 1)
                logger.info("Forward from z=%d (%d frames)", fwd_start, fwd_max)
                for frame_idx, obj_ids, masks in self.predictor.propagate_in_video(
                    inference_state=self.inference_state,
                    start_frame_idx=fwd_start,
                    max_frame_num_to_track=fwd_max,
                    reverse=False,
                ):
                    fi = int(frame_idx)
                    if z_lo <= fi <= z_hi:
                        for oid, mask in self._collect_frame_masks(obj_ids, masks).items():
                            forward.setdefault(oid, {})[fi] = mask

            if direction in ("backward", "both"):
                bwd_max = max(1, bwd_start - z_lo + 1)
                logger.info("Backward from z=%d (%d frames)", bwd_start, bwd_max)
                for frame_idx, obj_ids, masks in self.predictor.propagate_in_video(
                    inference_state=self.inference_state,
                    start_frame_idx=bwd_start,
                    max_frame_num_to_track=bwd_max,
                    reverse=True,
                ):
                    fi = int(frame_idx)
                    if z_lo <= fi <= z_hi:
                        for oid, mask in self._collect_frame_masks(obj_ids, masks).items():
                            backward.setdefault(oid, {})[fi] = mask

        # NOTE: the MTS original this was ported from had `oid` here leaking
        # out of a separate `for oid in all_obj_ids: merged[oid] = {}` loop
        # above (no nested iteration), so it only ever merged one arbitrary
        # object's masks no matter how many were requested. Fixed here by
        # actually nesting the fi-loop per object.
        all_obj_ids = set(forward) | set(backward)
        merged: dict[int, dict[int, np.ndarray]] = {}
        for oid in all_obj_ids:
            merged[oid] = {}
            for fi in range(z_lo, z_hi + 1):
                fwd_mask = forward.get(oid, {}).get(fi)
                bwd_mask = backward.get(oid, {}).get(fi)
                combined: np.ndarray | None = None
                if fwd_mask is not None:
                    combined = np.asarray(fwd_mask, dtype=bool)
                if bwd_mask is not None:
                    bwd = np.asarray(bwd_mask, dtype=bool)
                    combined = bwd if combined is None else (combined | bwd)
                if combined is not None and combined.any():
                    merged[oid][fi] = combined

        total_slices = sum(len(v) for v in merged.values())
        logger.info(
            "Multi-object propagation finished: %d objects, %d slice-masks",
            len(merged),
            total_slices,
        )
        return merged

    @staticmethod
    def _mask_tensor_to_numpy(masks, obj_index: int = 0) -> np.ndarray:
        """Convert SAM 2 video_res_masks logits to a boolean HxW mask."""
        mask_logits = masks[obj_index]
        if mask_logits.ndim == 3:
            mask_logits = mask_logits.squeeze(0)
        return (mask_logits > 0.0).detach().cpu().numpy().astype(bool)
