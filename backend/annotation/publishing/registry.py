"""Provider selection for publishing (``settings.MITO_PUBLISHING_PROVIDER``)."""

from __future__ import annotations

from django.conf import settings
from django.utils.module_loading import import_string

from .interfaces import PublishingProvider

PUBLISHING_PROVIDERS: dict[str, str] = {
    "placeholder": "annotation.publishing.adapters.placeholder.PlaceholderPublishingProvider",
    "mitoverse": "annotation.publishing.adapters.mitoverse.MitoVersePublishingProvider",
}

DEFAULT_PUBLISHING_PROVIDER = "placeholder"


def get_publishing_provider(name: str | None = None) -> PublishingProvider:
    name = name or getattr(
        settings, "MITO_PUBLISHING_PROVIDER", DEFAULT_PUBLISHING_PROVIDER
    )
    try:
        dotted = PUBLISHING_PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown publishing provider '{name}'. "
            f"Known: {sorted(PUBLISHING_PROVIDERS)}"
        ) from exc
    return import_string(dotted)()
