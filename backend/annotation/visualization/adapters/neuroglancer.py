"""Basic Neuroglancer visualization provider.

Builds a Neuroglancer view URL from a configured base and the volume's image
(and label, when present) paths. This is a *viewer* only. The URL scheme here is
intentionally simple (base + encoded source query); a real deployment would
build a full Neuroglancer state fragment.
"""

from __future__ import annotations

from urllib.parse import quote

from django.conf import settings

from ..interfaces import VisualizationProvider, _resolve_volume


def build_neuroglancer_url(volume, base: str | None = None) -> str:
    """Return a Neuroglancer URL for ``volume`` or ``""`` when not configured."""
    base = base if base is not None else getattr(
        settings, "MITO_NEUROGLANCER_BASE_URL", ""
    )
    if not base or volume is None:
        return ""
    image = getattr(volume, "image_location", "")
    if not image:
        return ""
    sep = "&" if "?" in base else "?"
    url = f"{base}{sep}image={quote(str(image), safe='')}"
    region_mask = getattr(volume, "region_mask_location", "")
    if region_mask:
        url += f"&region_mask={quote(str(region_mask), safe='')}"
    label = getattr(volume, "label_location", "")
    if label:
        url += f"&label={quote(str(label), safe='')}"
    return url


class NeuroglancerVisualizationProvider(VisualizationProvider):
    name = "neuroglancer"

    def get_view_url(self, volume_or_task) -> str:
        return build_neuroglancer_url(_resolve_volume(volume_or_task))
