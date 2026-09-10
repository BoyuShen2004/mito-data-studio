"""Lazy singleton loader for the interactive mask model.

Point Mask / Box Mask / Boundary run on SAM 2 — the same hiera_large
checkpoint Track propagates with, shared rather than loaded twice (see
``sam2_masks``). ``torch``/``sam2`` are imported lazily, never at Django
startup, so the rest of the app and the test suite can still import this
package on a machine with no GPU. A view turns :class:`AiUnavailable` into a
clear 503 when the runtime or its weights are missing.

There is deliberately **no fallback model**. This path used to drop to a
small CPU ONNX model (EfficientSAM) when SAM 2 could not start, which meant
an environment problem showed up as quietly worse masks — fragments of large
mitochondria instead of the object — rather than as an error anyone could
act on. Failing loudly is the honest behaviour: the tools are unavailable,
manual annotation is not affected, and the cause is in the message.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

_mask_model = None
_load_error: str | None = None
_mask_model_lock = threading.Lock()

_log = logging.getLogger(__name__)

# Phrases that mean "try again", not "this host cannot run the tools".
_TRANSIENT_LOAD_MARKERS = (
    "out of memory",
    "cuda out of memory",
    "cuda error",
    "cuda busy",
    "cudnn",
    "cublas",
    "device-side assert",
    "resource exhausted",
)


class AiUnavailable(Exception):
    """Raised when the interactive AI-mask tools can't run right now
    (missing dependency, no GPU, missing model weights, or a transient GPU
    fault) — never a bug, just an environment that hasn't installed the
    optional extra or is momentarily busy."""


def _is_transient_load_failure(exc: BaseException) -> bool:
    """True when a later retry on this worker may succeed.

    Permanent failures (no CUDA, missing checkpoint) stay sticky so we do not
    re-import torch on every click. OOM / CUDA races during multi-worker boot
    must *not* stick: otherwise one unlucky first click brands the worker
    unavailable until gunicorn recycles it.
    """
    if type(exc).__name__ in {"OutOfMemoryError", "CUDAOutOfMemoryError"}:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_LOAD_MARKERS)


@contextmanager
def _cross_process_load_lock():
    """Serialize the ~5 s checkpoint load across gunicorn workers.

    Each worker still keeps its own in-process model afterwards — this lock
    only covers construction. Without it, N workers race onto one GPU at boot
    (and again on the first Point Mask click if preload missed), which is how
    an 11 GiB card OOMs while loading three ~2 GB copies at once. Prompt
    latency after the model is resident is unchanged: the lock is not held
    during encode/decode.
    """
    import fcntl
    import tempfile

    from django.conf import settings

    root = Path(getattr(settings, "MITO_DATA_ROOT", "") or tempfile.gettempdir())
    lock_dir = root / ".mito"
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "sam2-model-load.lock"
        handle = lock_path.open("a+b")
    except OSError:
        # A read-only data root must not block AI tools; fall back to process-
        # local loading (in-process lock still serializes threads).
        yield
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def get_mask_model():
    """The model backing Point Mask / Box Mask / Boundary."""
    global _mask_model, _load_error
    if _mask_model is not None:
        return _mask_model
    if _load_error is not None:
        raise AiUnavailable(_load_error)
    # Gunicorn has multiple request threads per worker; build once and reuse
    # it together with its warm feature cache.
    with _mask_model_lock:
        if _mask_model is not None:
            return _mask_model
        if _load_error is not None:
            raise AiUnavailable(_load_error)
        return _load_mask_model()


def _load_mask_model():
    global _mask_model, _load_error

    try:
        with _cross_process_load_lock():
            # Another thread may have finished while we waited for the flock.
            if _mask_model is not None:
                return _mask_model
            from annotation.tracking.registry import get_tracking_provider

            from .sam2_feature_cache import DiskFeatureStore
            from .sam2_masks import Sam2Masks

            wrapper = get_tracking_provider("sam2")._load()
            # The L2 behind each worker's in-process LRU. Without it every worker
            # re-encodes slices its siblings already did, which is what kept
            # scrubbing intermittently slow.
            wrapper.set_feature_store(DiskFeatureStore())
            _mask_model = Sam2Masks(wrapper)
    except Exception as exc:  # noqa: BLE001 — any load failure is the same 503
        message = (
            "Interactive AI mask tools (Point Mask / Box Mask / Boundary) need "
            f"SAM 2 with CUDA and its checkpoint: {exc}"
        )
        if _is_transient_load_failure(exc):
            _log.warning("Transient SAM 2 load failure (will retry): %s", exc)
            raise AiUnavailable(
                f"{message} The GPU was busy — retry in a moment."
            ) from exc
        _load_error = message
        raise AiUnavailable(_load_error) from exc
    _log.info("Interactive mask tools using SAM 2 (shared with Track).")
    return _mask_model


def reset_mask_model() -> None:
    """Drop the process-local mask backend (tests/maintenance only)."""
    global _mask_model, _load_error
    with _mask_model_lock:
        _mask_model = None
        _load_error = None
