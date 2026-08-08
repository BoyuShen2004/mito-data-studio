"""Publishing provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class PublishingProvider(ABC):
    name: str = "base"

    @abstractmethod
    def publish_result(self, volume_or_project) -> dict:
        """Publish a completed volume or project. Returns a structured result."""

    def rebuild_catalog(self) -> dict:
        """Rebuild any local/derived catalog of published results."""
        return {"rebuilt": False, "detail": "Not implemented."}

    def sync_external_catalog(self) -> dict:
        """Push local published state to an external catalog."""
        return {"synced": False, "detail": "Not implemented."}
