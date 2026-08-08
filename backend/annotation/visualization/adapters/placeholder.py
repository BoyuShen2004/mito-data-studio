"""Placeholder visualization provider — reports no viewer configured."""

from __future__ import annotations

from ..interfaces import VisualizationProvider


class PlaceholderVisualizationProvider(VisualizationProvider):
    name = "placeholder"

    def get_view_url(self, volume_or_task) -> str:
        return ""

    def get_view_state(self, volume_or_task) -> dict:
        state = super().get_view_state(volume_or_task)
        state["message"] = "No visualization provider is configured."
        return state
