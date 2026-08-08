"""Centralised display-label mapping (internal value -> user-facing label).

Donglai's conceptual design calls the data-owning role an **Institution**; the
codebase keeps the stable internal role value ``requester`` (renaming it would
churn migrations, auth, APIs, tests, and existing rows for no functional gain).
This module is the single place that maps internal identifiers to the words
shown to users, so the UI can say "Institution" while the database still says
``requester``.

Keep this in sync with the frontend mirror in ``frontend/src/labels.ts``.
"""

from __future__ import annotations

from .choices import UserRole

# Role value -> what a human should see. ``requester``/``client`` display as
# "Institution"; the internal values are unchanged.
ROLE_DISPLAY_LABELS: dict[str, str] = {
    UserRole.MANAGER: "Manager",
    UserRole.ANNOTATOR: "Annotator",
    UserRole.REQUESTER: "Institution",
    UserRole.CLIENT: "Institution",
    UserRole.REVIEWER: "Reviewer",
}

# Domain nouns whose display label may diverge from the internal name.
TERM_DISPLAY_LABELS: dict[str, str] = {
    "requester": "Institution",
    "project": "Project",
    "dataset": "Dataset",
    "volume": "Volume",
    "chunk": "Chunk",
    "task": "Task",
    "submission": "Submission",
    "review": "Review",
}


def role_label(role: str | None) -> str:
    """User-facing label for an internal role value."""
    if not role:
        return ""
    return ROLE_DISPLAY_LABELS.get(role, role.replace("_", " ").title())


def term_label(term: str) -> str:
    """User-facing label for an internal domain term."""
    return TERM_DISPLAY_LABELS.get(term, term.replace("_", " ").title())
