"""Provider selection for quality control.

The active provider is chosen by ``settings.MITO_QC_PROVIDER`` (default
``basic``). Adapters are referenced by dotted path so importing this registry
does not import every adapter (and their optional heavy deps) eagerly.
"""

from __future__ import annotations

from django.conf import settings
from django.utils.module_loading import import_string

from .interfaces import QualityControlProvider

# Registry key -> dotted path of a QualityControlProvider subclass.
QC_PROVIDERS: dict[str, str] = {
    "basic": "annotation.quality_control.adapters.basic.BasicQualityControlProvider",
    "connected_components": (
        "annotation.quality_control.adapters.connected_components."
        "ConnectedComponentsQualityControlProvider"
    ),
}

DEFAULT_QC_PROVIDER = "basic"


def get_qc_provider(name: str | None = None) -> QualityControlProvider:
    """Return an instance of the configured (or named) QC provider."""
    name = name or getattr(settings, "MITO_QC_PROVIDER", DEFAULT_QC_PROVIDER)
    try:
        dotted = QC_PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown QC provider '{name}'. Known: {sorted(QC_PROVIDERS)}"
        ) from exc
    return import_string(dotted)()
