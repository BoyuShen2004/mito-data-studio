"""Phase 7 — work sessions and measured active time.

Phase 6 reports `mean_elapsed_*`: wall-clock between recorded events. A task
assigned Friday and submitted Monday reads three days regardless of effort.
This module measures the other thing — **how long someone was actually
working** — and keeps it strictly separate so the two can never be confused.

The definition, precisely (ADR-005 §7):

    active time = the sum of server-measured intervals between heartbeats from
    an open session, where each interval is capped at
    MITO_SESSION_MAX_HEARTBEAT_SECONDS and any gap longer than
    MITO_SESSION_IDLE_TIMEOUT_SECONDS credits nothing.

Consequences worth stating rather than discovering later:

* **Client clocks are never trusted for duration.** `client_ts` is recorded for
  diagnostics only. Every credited second comes from `timezone.now()`, because
  a wrong or hostile clock must not be able to invent work.
* **A sleeping tab cannot bank time.** Waking after an hour credits the cap,
  not the hour.
* **Two tabs are not two people.** Sessions overlap freely, but aggregation
  merges overlapping intervals per (user, task), so one hour with two tabs open
  is one hour.
* **Legacy work has no active time and none is invented.** Aggregates report
  coverage so a caller can see how much of the work is actually measured.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import AnnotationTask, WorkSession

logger = logging.getLogger(__name__)


class SessionError(Exception):
    def __init__(self, message: str, *, reason: str = "invalid"):
        super().__init__(message)
        self.reason = reason


def sessions_enabled() -> bool:
    """Shares Phase 7's flag: the session *is* the op log's owner (doc 16)."""
    return bool(getattr(settings, "FEATURE_ANNOTATION_OPS", False))


def _require_enabled() -> None:
    if not sessions_enabled():
        raise SessionError(
            "Work sessions are disabled (FEATURE_ANNOTATION_OPS).",
            reason="disabled",
        )


def _max_interval() -> int:
    return int(getattr(settings, "MITO_SESSION_MAX_HEARTBEAT_SECONDS", 120))


def _idle_timeout() -> int:
    return int(getattr(settings, "MITO_SESSION_IDLE_TIMEOUT_SECONDS", 300))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def start_session(*, task: AnnotationTask, actor) -> WorkSession:
    """Open an editing session. Returns the session the client heartbeats on.

    Deliberately does **not** close the actor's other open sessions on this
    task: two tabs are legitimate, and forcing one closed would lose the work
    the other is doing. Overlap is handled at aggregation instead, which is the
    only place it can be handled correctly.
    """
    _require_enabled()
    now = timezone.now()
    return WorkSession.objects.create(task=task, actor=actor, last_heartbeat_at=now)


def credited_seconds(previous, now, *, max_interval=None, idle_timeout=None) -> int:
    """How much of ``now - previous`` counts as work.

    Pure and side-effect free so the policy can be tested directly rather than
    only through its effects.

    * no previous heartbeat -> 0 (nothing to measure from)
    * gap > idle timeout    -> 0 (they were away; a new span starts)
    * otherwise             -> min(gap, cap), never negative
    """
    if previous is None:
        return 0
    max_interval = _max_interval() if max_interval is None else max_interval
    idle_timeout = _idle_timeout() if idle_timeout is None else idle_timeout

    delta = (now - previous).total_seconds()
    if delta <= 0:
        # Clock skew or a duplicate heartbeat. Credit nothing rather than
        # letting a negative interval subtract real work.
        return 0
    if delta > idle_timeout:
        return 0
    return int(min(delta, max_interval))


@transaction.atomic
def heartbeat(session: WorkSession, *, actor=None, client_ts=None) -> WorkSession:
    """Credit the interval since the last heartbeat and record a new one.

    Locks the session row: two tabs sharing a session id (or a client retrying)
    must not both credit the same interval.
    """
    _require_enabled()

    locked = WorkSession.objects.select_for_update().filter(pk=session.pk).first()
    if locked is None:
        raise SessionError("Session no longer exists.", reason="gone")
    if actor is not None and locked.actor_id != getattr(actor, "id", None):
        raise SessionError("Not your session.", reason="forbidden")
    if not locked.is_open:
        raise SessionError("Session is already closed.", reason="closed")

    now = timezone.now()
    gained = credited_seconds(locked.last_heartbeat_at, now)
    locked.active_seconds = int(locked.active_seconds) + gained
    locked.last_heartbeat_at = now
    locked.heartbeats = int(locked.heartbeats) + 1
    locked.save(update_fields=["active_seconds", "last_heartbeat_at", "heartbeats"])
    return locked


@transaction.atomic
def end_session(session: WorkSession, *, actor=None) -> WorkSession:
    """Close a session, crediting the final interval.

    Idempotent: closing an already-closed session returns it unchanged rather
    than raising, because a client that retries its own close is not in error.
    """
    _require_enabled()

    locked = WorkSession.objects.select_for_update().filter(pk=session.pk).first()
    if locked is None:
        raise SessionError("Session no longer exists.", reason="gone")
    if actor is not None and locked.actor_id != getattr(actor, "id", None):
        raise SessionError("Not your session.", reason="forbidden")
    if not locked.is_open:
        return locked

    now = timezone.now()
    locked.active_seconds = int(locked.active_seconds) + credited_seconds(
        locked.last_heartbeat_at, now
    )
    locked.ended_at = now
    locked.save(update_fields=["active_seconds", "ended_at"])
    return locked


def close_stale_sessions(*, now=None, limit: int = 500) -> int:
    """Close sessions whose client stopped heartbeating. Returns how many.

    This is the browser-crash and closed-laptop path. Nothing after the last
    heartbeat is credited, so a session that died an hour ago contributes
    exactly what it had earned when it went quiet.

    Bounded per sweep so it can run on a timer without becoming a long
    transaction.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=_idle_timeout())
    stale = list(
        WorkSession.objects.filter(
            ended_at__isnull=True, last_heartbeat_at__lt=cutoff
        ).values_list("pk", flat=True)[:limit]
    )
    if not stale:
        return 0
    # ended_at is set to the last heartbeat, not to now: the time between going
    # quiet and being swept is not work.
    closed = 0
    for pk in stale:
        with transaction.atomic():
            s = WorkSession.objects.select_for_update().filter(pk=pk).first()
            if s is None or not s.is_open:
                continue
            s.ended_at = s.last_heartbeat_at or s.started_at
            s.save(update_fields=["ended_at"])
            closed += 1
    return closed


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _merge_intervals(spans) -> int:
    """Total seconds covered by ``spans``, counting overlap once.

    This is what stops two open tabs reporting double time. Sessions are
    intervals on a line; the union of those intervals is the answer, not their
    sum.
    """
    if not spans:
        return 0
    spans = sorted(spans, key=lambda s: s[0])
    total = 0
    cur_start, cur_end = spans[0]
    for start, end in spans[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = start, end
    total += (cur_end - cur_start).total_seconds()
    return int(max(total, 0))


def active_seconds_for(*, actor=None, task=None, project=None,
                       deduplicate_overlap: bool = True) -> int:
    """Measured active seconds, optionally de-duplicated across overlaps.

    With ``deduplicate_overlap`` (the default) the answer is the union of each
    actor's session spans — two tabs open for an hour is an hour. Without it,
    the raw sum, which is only ever useful for diagnosing a client that opens
    too many sessions.

    De-duplication necessarily uses session *spans* rather than credited
    seconds, so an actor who idles inside an overlapping pair can report
    slightly more here than the capped sum. That is the correct trade: the
    alternative double-counts, which is the failure the instruction names.
    """
    qs = WorkSession.objects.all()
    if actor is not None:
        qs = qs.filter(actor=actor)
    if task is not None:
        qs = qs.filter(task=task)
    if project is not None:
        qs = qs.filter(task__project=project)

    if not deduplicate_overlap:
        return int(qs.aggregate(n=Sum("active_seconds"))["n"] or 0)

    per_actor: dict[int, list] = {}
    for s in qs.only("actor_id", "started_at", "last_heartbeat_at", "ended_at",
                     "active_seconds"):
        end = s.ended_at or s.last_heartbeat_at or s.started_at
        if end < s.started_at:
            continue
        per_actor.setdefault(s.actor_id, []).append((s.started_at, end))
    return sum(_merge_intervals(spans) for spans in per_actor.values())


def task_active_time(task: AnnotationTask) -> dict:
    """Active-time summary for one task, with coverage.

    ``coverage`` answers "is this number meaningful?" — legacy work predates
    sessions entirely, and reporting 0 seconds without saying so would look
    like "nobody worked on it" rather than "we were not measuring".
    """
    sessions = WorkSession.objects.filter(task=task)
    n = sessions.count()
    return {
        "active_seconds": active_seconds_for(task=task),
        "raw_active_seconds": active_seconds_for(task=task,
                                                 deduplicate_overlap=False),
        "sessions": n,
        "measured": n > 0,
    }


def project_active_time(project) -> dict:
    """Active-time summary for a project, with the same coverage caveat."""
    from annotation.models import AnnotationTask as _T

    total_tasks = _T.objects.filter(project=project).count()
    measured_tasks = (
        WorkSession.objects.filter(task__project=project)
        .values("task_id").distinct().count()
    )
    return {
        "active_seconds": active_seconds_for(project=project),
        "tasks_total": total_tasks,
        "tasks_measured": measured_tasks,
        # 0.0..1.0 — what fraction of the project's tasks have any session data.
        "coverage": (
            round(measured_tasks / total_tasks, 4) if total_tasks else 0.0
        ),
    }
