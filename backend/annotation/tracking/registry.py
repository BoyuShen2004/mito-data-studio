"""Provider selection for SAM2 tracking (``settings.MITO_TRACKING_PROVIDER``)."""

from __future__ import annotations

import threading

from django.conf import settings
from django.utils.module_loading import import_string

from .interfaces import TrackingProvider

TRACKING_PROVIDERS: dict[str, str] = {
    "local": "annotation.tracking.adapters.local.LocalTrackingProvider",
    "sam2": "annotation.tracking.adapters.sam2.Sam2TrackingProvider",
}

DEFAULT_TRACKING_PROVIDER = "local"
_providers: dict[str, TrackingProvider] = {}
_providers_lock = threading.Lock()


def get_tracking_provider(name: str | None = None) -> TrackingProvider:
    name = name or getattr(
        settings, "MITO_TRACKING_PROVIDER", DEFAULT_TRACKING_PROVIDER
    )
    if name == "sam2":
        try:
            import torch  # noqa: F401 — SAM2 needs this at load time
        except ImportError:
            import logging

            logging.getLogger(__name__).warning(
                "MITO_TRACKING_PROVIDER=sam2 but torch is not installed; "
                "falling back to 'local'. Fix with: "
                "conda env update -f environment.yml --prune"
            )
            name = "local"
    try:
        dotted = TRACKING_PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown tracking provider '{name}'. Known: {sorted(TRACKING_PROVIDERS)}"
        ) from exc
    # Model-backed providers are process singletons. Gunicorn workers still
    # own independent CUDA models, but repeat propagates in one worker reuse
    # the already-loaded checkpoint instead of reloading ~900 MB each time.
    with _providers_lock:
        provider = _providers.get(name)
        if provider is None:
            provider = import_string(dotted)()
            _providers[name] = provider
        return provider


def reset_tracking_providers() -> None:
    """Drop process-local providers (tests/maintenance only)."""
    with _providers_lock:
        _providers.clear()
