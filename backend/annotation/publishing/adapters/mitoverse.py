"""MitoVerse publishing provider — integration stub.

This is where production MitoVerse publication (catalog entry, precomputed
conversion, Hugging Face sync) will live. It intentionally raises so a
misconfiguration surfaces loudly instead of silently pretending to publish.
"""

from __future__ import annotations

from ..interfaces import PublishingProvider


class MitoVersePublishingProvider(PublishingProvider):
    name = "mitoverse"

    def publish_result(self, volume_or_project) -> dict:
        raise NotImplementedError(
            "MitoVerse publishing is not implemented yet. Configure "
            "MITO_PUBLISHING_PROVIDER=placeholder for the MVP."
        )
