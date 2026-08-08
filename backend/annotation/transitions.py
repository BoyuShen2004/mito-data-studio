"""Phase 5 — the legal task-status transition graph, declared once.

Doc 16 specifies an explicit transition table with illegal jumps forbidden "in
the service layer **and** DB constraints where feasible". This is the service
half; ADR-003 §2 records why the database half stops at the single-row
constraints PostgreSQL can actually express.

A ``CHECK`` constraint sees only the row being written, never the row it
replaces, so it cannot express *"submitted may become approved"*. Enforcing that
in the database needs a trigger comparing OLD and NEW — deliberately not added:
triggers are invisible to Django's migration state, they fire during
``loaddata`` (a failure mode this repository has already been bitten by once),
and every write path already funnels through the service layer.

Shipped inert. With ``FEATURE_REVIEW_HISTORY`` off, an illegal transition is
logged and permitted; with it on, it raises. That is the same expand-contract
posture as every prior phase: an existing deployment carrying historically odd
data cannot be broken merely by deploying the code.
"""

from __future__ import annotations

import logging

from django.conf import settings

from core.choices import TaskStatus

logger = logging.getLogger(__name__)


class IllegalTransition(Exception):
    """A task-status change the transition table does not permit."""

    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(
            f"Illegal task status transition: {current!r} -> {target!r}. "
            f"Allowed from {current!r}: "
            f"{sorted(ALLOWED_TRANSITIONS.get(current, set())) or 'nothing'}."
        )


# Doc 16's assignment state machine
#   pending -> claimed -> in_progress -> submitted -> under_review
#           -> approved | rejected | revision_requested -> (resubmit loop)
# mapped onto mito's TaskStatus, which has no separate `under_review` (review is
# an event, not a dwell state) and folds `pending`/`claimed` into
# `unassigned`/`assigned`.
#
# Kept deliberately permissive in the "back to the annotator" direction: mito's
# product rule is that reject and revision do not gate further work, and a
# manager may unlock an approved task without inventing a second review round
# (`set_task_annotation_lock`). Narrowing that would fail the phase gate, which
# is parity with current UX.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    TaskStatus.UNASSIGNED: {
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
    },
    TaskStatus.ASSIGNED: {
        TaskStatus.IN_PROGRESS,
        TaskStatus.SUBMITTED,
        TaskStatus.UNASSIGNED,       # unassign / reclaim / lease expiry
    },
    TaskStatus.IN_PROGRESS: {
        TaskStatus.SUBMITTED,
        TaskStatus.ASSIGNED,
        TaskStatus.UNASSIGNED,
    },
    TaskStatus.SUBMITTED: {
        TaskStatus.APPROVED,
        TaskStatus.REJECTED,
        TaskStatus.REVISION_REQUESTED,
        TaskStatus.IN_PROGRESS,      # annotator resumes before a verdict lands
        TaskStatus.UNASSIGNED,       # reclaimed while awaiting review
    },
    TaskStatus.APPROVED: {
        # Only reachable when a manager approved with "allow further
        # annotation", or unlocked afterwards. Approved is not terminal in mito.
        TaskStatus.IN_PROGRESS,
        TaskStatus.SUBMITTED,
    },
    TaskStatus.REJECTED: {
        TaskStatus.IN_PROGRESS,
        TaskStatus.SUBMITTED,
        TaskStatus.ASSIGNED,
        TaskStatus.UNASSIGNED,
    },
    TaskStatus.REVISION_REQUESTED: {
        TaskStatus.IN_PROGRESS,
        TaskStatus.SUBMITTED,
        TaskStatus.ASSIGNED,
        TaskStatus.UNASSIGNED,
    },
}


def transitions_enforced() -> bool:
    """Is an illegal transition an error, or merely a log line?"""
    return bool(getattr(settings, "FEATURE_REVIEW_HISTORY", False))


def is_legal(current: str | None, target: str) -> bool:
    """Is ``current -> target`` permitted?

    A self-transition is always legal: re-saving a task in the state it already
    holds is idempotent, and forbidding it would turn a harmless double-submit
    into an error. An unknown or empty ``current`` is treated as legal — a row
    whose status predates this table must not become unwritable.
    """
    if not current or current == target:
        return True
    allowed = ALLOWED_TRANSITIONS.get(current)
    if allowed is None:
        logger.debug("No transition rule for status %r; permitting.", current)
        return True
    return target in allowed


def assert_transition(current: str | None, target: str) -> None:
    """Raise :class:`IllegalTransition` if the move is not permitted.

    Inert unless the flag is on, so deploying this code cannot break a
    deployment whose historical data disagrees with the table. The log line is
    the point of the inert phase: it shows which transitions really occur
    before any of them start failing.
    """
    if is_legal(current, target):
        return
    if not transitions_enforced():
        logger.warning(
            "Illegal task status transition %r -> %r permitted "
            "(FEATURE_REVIEW_HISTORY is off).",
            current, target,
        )
        return
    raise IllegalTransition(current, target)


def legal_targets(current: str | None) -> set[str]:
    """What ``current`` may become. Useful for UI and for tests."""
    if not current:
        return set(ALLOWED_TRANSITIONS)
    return set(ALLOWED_TRANSITIONS.get(current, set())) | {current}
