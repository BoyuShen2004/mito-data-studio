"""Placeholder publishing provider.

Records the intent to publish (and a light manifest) without contacting any
external service. Real MitoVerse / Hugging Face publication is future work.
"""

from __future__ import annotations

from ..interfaces import PublishingProvider


class PlaceholderPublishingProvider(PublishingProvider):
    name = "placeholder"

    def publish_result(self, volume_or_project) -> dict:
        title = getattr(volume_or_project, "title", None) or getattr(
            volume_or_project, "name", str(volume_or_project)
        )
        return {
            "published": False,
            "provider": self.name,
            "target": title,
            "detail": "Publishing is not configured; recorded intent only.",
        }
