"""Visualization provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class VisualizationProvider(ABC):
    name: str = "base"

    @abstractmethod
    def get_view_url(self, volume_or_task) -> str:
        """Return a viewer URL for a volume or task, or ``""`` if unavailable."""

    def get_view_state(self, volume_or_task) -> dict:
        """Return a structured view descriptor (source paths, region, status)."""
        volume = _resolve_volume(volume_or_task)
        state = {
            "available": bool(self.get_view_url(volume_or_task)),
            "provider": self.name,
            "image_path": getattr(volume, "image_location", ""),
            "region_mask_path": getattr(volume, "region_mask_location", ""),
            "label_path": getattr(volume, "label_location", ""),
        }
        region = _resolve_region(volume_or_task)
        if region:
            state["region"] = region
        return state


def _resolve_volume(volume_or_task):
    """Accept either a Volume or a Task and return the underlying Volume."""
    return getattr(volume_or_task, "volume", volume_or_task)


def _resolve_region(volume_or_task) -> dict | None:
    task = volume_or_task if hasattr(volume_or_task, "z_start") else None
    if task is None:
        return None
    return {
        "z_start": task.z_start,
        "z_end": task.z_end,
        "y_start": task.y_start,
        "y_end": task.y_end,
        "x_start": task.x_start,
        "x_end": task.x_end,
    }
