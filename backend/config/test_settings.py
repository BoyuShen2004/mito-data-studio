"""Hermetic local test overrides; never used by the deployed service."""

from pathlib import Path

from .settings import *  # noqa: F403

STATIC_ROOT = Path("/tmp/mito-data-agent-staticfiles")
