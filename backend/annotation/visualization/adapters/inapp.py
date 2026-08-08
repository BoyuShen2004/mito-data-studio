"""In-app slice-viewer visualization provider.

Points the client at the SPA's built-in slice viewer, which streams slices from
the server's slice-IO endpoints (``annotation.visualization.slice_io``) on
demand. No external tool required; works for every role (read-only viewing).
"""

from __future__ import annotations

from ..interfaces import VisualizationProvider, _resolve_volume, _resolve_region
from ..slice_io import volume_meta


class InAppVisualizationProvider(VisualizationProvider):
    name = "inapp"

    def get_view_url(self, volume_or_task) -> str:
        # A task carries its own z-range; a bare volume opens whole.
        if hasattr(volume_or_task, "z_start"):
            return f"/viewer/tasks/{volume_or_task.id}"
        return f"/viewer/volumes/{volume_or_task.id}"

    def get_view_state(self, volume_or_task) -> dict:
        state = super().get_view_state(volume_or_task)
        state["mode"] = "slice_viewer"
        volume = _resolve_volume(volume_or_task)
        state["volume_id"] = getattr(volume, "id", None)
        try:
            if volume.image_location:
                state["meta"] = volume_meta(volume.image_location)
                state["available"] = True
        except Exception as exc:  # unreadable file → still report, just no meta
            state["message"] = f"Slice metadata unavailable: {exc}"
        region = _resolve_region(volume_or_task)
        if region:
            state["region"] = region
        state["has_region_mask"] = bool(
            getattr(volume, "region_mask_location", "")
        )
        state["has_label"] = bool(getattr(volume, "label_location", ""))
        return state
