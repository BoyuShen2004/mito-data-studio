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

import threading

_mask_model = None
_load_error: str | None = None
_mask_model_lock = threading.Lock()


class AiUnavailable(Exception):
    """Raised when the interactive AI-mask tools can't run right now
    (missing dependency, no GPU, or missing model weights) — never a bug,
    just an environment that hasn't installed the optional extra."""


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
        _load_error = (
            "Interactive AI mask tools (Point Mask / Box Mask / Boundary) need "
            f"SAM 2 with CUDA and its checkpoint: {exc}"
        )
        raise AiUnavailable(_load_error) from exc
    import logging

    logging.getLogger(__name__).info(
        "Interactive mask tools using SAM 2 (shared with Track)."
    )
    return _mask_model


def reset_mask_model() -> None:
    """Drop the process-local mask backend (tests/maintenance only)."""
    global _mask_model, _load_error
    with _mask_model_lock:
        _mask_model = None
        _load_error = None
