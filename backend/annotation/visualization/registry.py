"""Provider selection for visualization (``settings.MITO_VISUALIZATION_PROVIDER``)."""

from __future__ import annotations

from django.conf import settings
from django.utils.module_loading import import_string

from .interfaces import VisualizationProvider

VISUALIZATION_PROVIDERS: dict[str, str] = {
    "placeholder": "annotation.visualization.adapters.placeholder.PlaceholderVisualizationProvider",
    "inapp": "annotation.visualization.adapters.inapp.InAppVisualizationProvider",
    "neuroglancer": "annotation.visualization.adapters.neuroglancer.NeuroglancerVisualizationProvider",
}

DEFAULT_VISUALIZATION_PROVIDER = "inapp"


def get_visualization_provider(name: str | None = None) -> VisualizationProvider:
    name = name or getattr(
        settings, "MITO_VISUALIZATION_PROVIDER", DEFAULT_VISUALIZATION_PROVIDER
    )
    try:
        dotted = VISUALIZATION_PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown visualization provider '{name}'. "
            f"Known: {sorted(VISUALIZATION_PROVIDERS)}"
        ) from exc
    return import_string(dotted)()
