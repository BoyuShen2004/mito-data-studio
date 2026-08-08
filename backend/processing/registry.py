"""Processing-backend selection (``settings.MITO_PROCESSING_BACKEND``)."""

from __future__ import annotations

from django.conf import settings
from django.utils.module_loading import import_string

from .interfaces import ProcessingBackend

PROCESSING_BACKENDS: dict[str, str] = {
    "local": "processing.adapters.local.LocalProcessingBackend",
    "slurm": "processing.adapters.slurm.SlurmProcessingBackend",
}

DEFAULT_PROCESSING_BACKEND = "local"


def get_processing_backend(name: str | None = None) -> ProcessingBackend:
    name = name or getattr(
        settings, "MITO_PROCESSING_BACKEND", DEFAULT_PROCESSING_BACKEND
    )
    try:
        dotted = PROCESSING_BACKENDS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown processing backend '{name}'. "
            f"Known: {sorted(PROCESSING_BACKENDS)}"
        ) from exc
    return import_string(dotted)()
