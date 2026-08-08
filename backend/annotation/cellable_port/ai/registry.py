"""Lazy singleton loader for the ported EfficientSAM model.

Mirrors ``annotation/tracking/registry.py``'s provider pattern: onnxruntime
/ scikit-image / scipy come from ``environment.yml`` and are imported
lazily — never at Django startup — so the rest of the app and the test
suite can still import this package. A view calls
:func:`get_efficient_sam` and turns :class:`AiUnavailable` into a clear
503 when weights or deps are missing.
"""

from __future__ import annotations

import os
import threading

from django.conf import settings

_model = None
_load_error: str | None = None
_model_lock = threading.Lock()


class AiUnavailable(Exception):
    """Raised when the interactive AI-mask tools can't run right now
    (missing dependency or missing model weights) — never a bug, just an
    environment that hasn't installed the optional extra."""


def get_efficient_sam():
    global _model, _load_error
    if _model is not None:
        return _model
    if _load_error is not None:
        raise AiUnavailable(_load_error)

    # Gunicorn has multiple request threads per worker. Construct the large
    # ONNX session pair once, then reuse it and its warm embedding cache.
    with _model_lock:
        if _model is not None:
            return _model
        if _load_error is not None:
            raise AiUnavailable(_load_error)
        return _load_efficient_sam()


def _load_efficient_sam():
    global _model, _load_error

    variant = getattr(settings, "MITO_EFFICIENT_SAM_VARIANT", "vits")
    root = getattr(settings, "MITO_CELLABLE_MODELS_ROOT", "")
    encoder = os.path.join(root, f"efficient_sam_{variant}_encoder.onnx")
    decoder = os.path.join(root, f"efficient_sam_{variant}_decoder.onnx")
    if not (os.path.exists(encoder) and os.path.exists(decoder)):
        _load_error = (
            f"EfficientSAM model files not found (looked for {encoder}). "
            "Set MITO_CELLABLE_MODELS_ROOT / MITO_EFFICIENT_SAM_VARIANT in "
            ".env, or place efficient_sam_vits_{encoder,decoder}.onnx under "
            "vendor/efficient_sam/ (see root README.md)."
        )
        raise AiUnavailable(_load_error)
    try:
        from .efficient_sam import EfficientSam

        _model = EfficientSam(encoder, decoder)
    except ImportError as exc:
        _load_error = (
            "Interactive AI mask tools (Point Mask / Box Mask / Boundary) "
            f"need onnxruntime + scikit-image + scipy — install via "
            f"environment.yml ({exc})."
        )
        raise AiUnavailable(_load_error) from exc
    return _model
