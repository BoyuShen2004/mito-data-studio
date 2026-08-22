"""Automatic annotation time tracking — server-authoritative, per volume.

What this measures, precisely:

    An eligible volume's annotation time is the **union**, per annotator, of the
    contiguous work intervals recorded while that annotator had the task open in
    the editable editor.

Every word of that is load-bearing:

* **eligible** — volumes that were already assigned when this feature shipped
  are ``LEGACY_EXEMPT`` and report ``-`` forever. Their annotation began
  unmeasured, and a partial total shown as a whole one is worse than an honest
  "unknown". See :class:`core.choices.TimeTracking`.
* **union** — two tabs, two browsers or two devices overlapping on the same
  task is still one hour of work. Summing per-session counters would say two.
  :func:`union_seconds` merges the spans instead.
* **contiguous work intervals** — not whole sessions. A session that idles
  through lunch has a hole in it, and
  :class:`~annotation.models.WorkInterval` records the hole rather than
  papering over it.
* **per annotator** — reassignment never moves history. Each interval keeps the
  actor who earned it, so a volume handed to a second annotator reports both
  contributions separately and credits neither to the other.
* **editable editor** — a read-only viewer, a manager looking at someone's
  volume, and the Details page all count for nothing. Only the assigned
  annotator, on a task they may actually paint, accrues time.

The client's role is to say "I am still here" on a cadence. It cannot propose a
duration, a start time, or an end time; every credited second comes from
``timezone.now()`` on the server. See :mod:`annotation.sessions` for the
crediting policy this builds on, and ``config/settings.py`` for the constants.

**Failure is never fatal.** Timing is bookkeeping about annotation, not part of
it. Callers in the annotation path use :func:`safely`, and the client treats
every timing request as fire-and-forget, so a timing outage degrades to "we
stopped measuring" and never to "you cannot save your work".
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.choices import TimeTracking

from .models import AnnotationTask, WorkInterval, WorkSession
from .sessions import credited_seconds

logger = logging.getLogger(__name__)


class TimingError(Exception):
    """A timing request that cannot be honoured. Never raised into a save path."""

    def __init__(self, message: str, *, reason: str = "invalid"):
        super().__init__(message)
        self.reason = reason


# ---------------------------------------------------------------------------
# Configuration — one source of truth, served to the client
# ---------------------------------------------------------------------------


def _setting(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def heartbeat_seconds() -> int:
    return max(1, _setting("MITO_TIME_TRACKING_HEARTBEAT_SECONDS", 30))


def hidden_grace_seconds() -> int:
    return max(0, _setting("MITO_TIME_TRACKING_HIDDEN_GRACE_SECONDS", 60))


def idle_seconds() -> int:
    return max(0, _setting("MITO_TIME_TRACKING_IDLE_SECONDS", 120))


def abandon_grace_seconds() -> int:
    return max(0, _setting("MITO_TIME_TRACKING_ABANDON_GRACE_SECONDS", 0))


def max_interval_seconds() -> int:
    return max(1, _setting("MITO_SESSION_MAX_HEARTBEAT_SECONDS", 120))


def server_idle_timeout_seconds() -> int:
    return max(1, _setting("MITO_SESSION_IDLE_TIMEOUT_SECONDS", 300))


def timing_config() -> dict:
    """The protocol constants, as the client should use them.

    Served rather than duplicated in TypeScript: a client heartbeating slower
    than the server's cap would silently lose real work on every beat, and the
    only way to guarantee that cannot happen is for one side to be told.
    """
    return {
        "heartbeat_seconds": heartbeat_seconds(),
        "hidden_grace_seconds": hidden_grace_seconds(),
        "idle_seconds": idle_seconds(),
        "abandon_grace_seconds": abandon_grace_seconds(),
        "max_interval_seconds": max_interval_seconds(),
        "server_idle_timeout_seconds": server_idle_timeout_seconds(),
    }


def safely(fn, *args, **kwargs):
    """Run a timing operation, swallowing (and logging) any failure.

    The wrapper exists so annotation code can record timing without ever
    inheriting timing's failure modes: Submit must succeed even if the timing
    tables are unreachable.
    """
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 — timing must never break annotation
        logger.warning("annotation timing operation failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Eligibility and permission
# ---------------------------------------------------------------------------


def volume_is_eligible(volume) -> bool:
    """Is this volume's annotation time measured at all?

    Reads durable state. Never infers eligibility from current assignment —
    that is the whole point of storing it (see :class:`core.choices.TimeTracking`).
    """
    if volume is None:
        return False
    return getattr(volume, "time_tracking", TimeTracking.ELIGIBLE) == TimeTracking.ELIGIBLE


def task_is_eligible(task: AnnotationTask) -> bool:
    return volume_is_eligible(getattr(task, "volume", None))


def is_timing_annotator(user, task: AnnotationTask) -> bool:
    """Is ``user`` the person whose editing time this task should record?

    Strictly the **assigned** annotator. ``can_edit_task`` is deliberately not
    enough on its own: it answers yes for every manager, and a manager opening
    someone's task to look at it must not accrue that annotator's time — nor
    time of their own against a task they were never assigned.
    """
    uid = getattr(user, "id", None)
    if uid is None or not getattr(user, "is_authenticated", False):
        return False
    return task.assigned_to_id == uid


def can_track_task(user, task: AnnotationTask) -> tuple[bool, str]:
    """``(allowed, reason)`` for starting or extending timing on ``task``.

    The reason is returned rather than raised because the status endpoint wants
    to explain "why is nothing being counted?" without treating it as an error.
    """
    from .services import can_annotate_task

    if not is_timing_annotator(user, task):
        return False, "not_assigned"
    if not task_is_eligible(task):
        return False, "legacy_exempt"
    if not can_annotate_task(user, task):
        # Approved-and-locked, or edit permission withdrawn.
        return False, "not_editable"
    return True, "ok"


# ---------------------------------------------------------------------------
# Interval maintenance
# ---------------------------------------------------------------------------


def effective_interval_end(interval, *, now=None, grace: int | None = None):
    """When an interval really stopped counting.

    A closed interval answers with its own ``ended_at``. An **open** one is the
    interesting case: it may belong to a live editor that heartbeated four
    seconds ago, or to a browser that crashed last Tuesday, and the row looks
    identical either way. Both are capped at the session's last accepted
    heartbeat plus :func:`abandon_grace_seconds` — so a live session simply
    lags reality by less than one heartbeat, and a dead one stops exactly where
    it went quiet instead of billing forever.

    Applied on **read** as well as by the sweep, so aggregates are correct even
    if no reconciliation has run.
    """
    if interval.ended_at is not None:
        return interval.ended_at
    now = now or timezone.now()
    grace = abandon_grace_seconds() if grace is None else grace
    session = interval.session
    anchor = getattr(session, "last_heartbeat_at", None) or interval.started_at
    capped = max(anchor, interval.started_at) + timedelta(seconds=grace)
    return min(capped, now) if now > interval.started_at else interval.started_at


def _open_interval(session) -> WorkInterval | None:
    return (
        WorkInterval.objects.filter(session=session, ended_at__isnull=True)
        .order_by("-started_at")
        .first()
    )


def _new_interval(session, *, at) -> WorkInterval:
    return WorkInterval.objects.create(
        session=session,
        task_id=session.task_id,
        volume_id=session.task.volume_id,
        actor_id=session.actor_id,
        started_at=at,
    )


def _close_interval(interval, *, at, reason: str) -> WorkInterval:
    interval.ended_at = max(at, interval.started_at)
    interval.close_reason = reason
    interval.save(update_fields=["ended_at", "close_reason", "updated_at"])
    return interval


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@transaction.atomic
def start_timing(*, task: AnnotationTask, actor, client_token: str = "") -> WorkSession:
    """Start or **resume** timing for one editor tab.

    Idempotent on ``client_token``: a retried start, a refresh that reuses the
    tab's stored token, or a duplicated request all resume the same session
    rather than opening a second one. A different tab (different token) gets its
    own session on purpose — two tabs are two clients, and their overlap is
    resolved correctly at aggregation instead of by pretending one of them does
    not exist.

    A resumed session gets a fresh interval when its last heartbeat is old
    enough that the gap would not have been credited anyway, so a refresh after
    lunch does not stitch lunch into the morning's work.
    """
    allowed, reason = can_track_task(actor, task)
    if not allowed:
        raise TimingError(f"Timing is not available for this task ({reason}).", reason=reason)

    now = timezone.now()
    token = (client_token or "")[:64]

    existing = None
    if token:
        existing = (
            WorkSession.objects.select_for_update()
            .filter(task=task, actor=actor, client_token=token, ended_at__isnull=True)
            .order_by("-started_at")
            .first()
        )
    if existing is not None:
        # Resuming: credit whatever the gap earns, exactly as a heartbeat would,
        # so a refresh neither loses the seconds before it nor invents any.
        return _advance(existing, now=now)

    session = WorkSession.objects.create(
        task=task, actor=actor, last_heartbeat_at=now, client_token=token
    )
    session.task = task  # avoid a refetch in _new_interval
    _new_interval(session, at=now)
    return session


@transaction.atomic
def _advance(session: WorkSession, *, now) -> WorkSession:
    """Credit the elapsed interval and keep the open interval in step.

    This is the one place the session counter and the interval record move
    together, so they cannot disagree about what was credited.
    """
    locked = (
        WorkSession.objects.select_for_update()
        .select_related("task")
        .filter(pk=session.pk)
        .first()
    )
    if locked is None:
        raise TimingError("Session no longer exists.", reason="gone")
    if not locked.is_open:
        raise TimingError("Session is already closed.", reason="closed")

    previous = locked.last_heartbeat_at
    gained = credited_seconds(
        previous,
        now,
        max_interval=max_interval_seconds(),
        idle_timeout=server_idle_timeout_seconds(),
    )
    current = _open_interval(locked)
    gap = (now - previous).total_seconds() if previous else 0.0

    # The branches mirror ``credited_seconds`` exactly, but compare against the
    # *thresholds* rather than against ``gained`` — ``gained`` is an integer, so
    # a 45.7 s gap credits 45 and comparing the two would declare every ordinary
    # heartbeat "capped" and fragment the interval on every beat.
    if current is None:
        # Only reachable if reconciliation closed the interval but left the
        # session open. Start a fresh stretch rather than losing the credit.
        _new_interval(locked, at=now)
    elif previous is None or gap <= 0:
        # Nothing to measure from, or a duplicate/out-of-order beat. The open
        # interval already covers this instant; leave it alone.
        pass
    elif gap > server_idle_timeout_seconds():
        # They were away. Close the stretch where it actually went quiet and
        # begin a new one now, so the gap is absent from the union rather than
        # buried inside one long interval.
        _close_interval(current, at=previous, reason=WorkInterval.CloseReason.IDLE)
        _new_interval(locked, at=now)
    elif gap > max_interval_seconds():
        # A sleeping tab woken between the cap and the idle timeout. Credit the
        # cap where it was earned, then start a new stretch at now — the middle
        # is not work and must not end up inside either interval.
        _close_interval(
            current,
            at=previous + timedelta(seconds=max_interval_seconds()),
            reason=WorkInterval.CloseReason.CAPPED,
        )
        _new_interval(locked, at=now)
    # Otherwise the beat is contiguous: the interval stays open and its
    # effective end follows ``last_heartbeat_at``, which is updated below.

    locked.active_seconds = int(locked.active_seconds) + gained
    locked.last_heartbeat_at = now
    locked.heartbeats = int(locked.heartbeats) + 1
    locked.save(
        update_fields=["active_seconds", "last_heartbeat_at", "heartbeats", "updated_at"]
    )
    return locked


def heartbeat_timing(*, session_id, actor, task: AnnotationTask) -> WorkSession:
    """"Still here." Credits the elapsed interval from the server clock only.

    Re-checks permission every beat, so losing the assignment or having the task
    locked stops the accumulation at the next beat rather than at the next page
    load.
    """
    allowed, reason = can_track_task(actor, task)
    if not allowed:
        raise TimingError(f"Timing is not available for this task ({reason}).", reason=reason)
    session = WorkSession.objects.filter(pk=session_id, task=task).first()
    if session is None:
        raise TimingError("Session no longer exists.", reason="gone")
    if session.actor_id != getattr(actor, "id", None):
        raise TimingError("Not your session.", reason="forbidden")
    return _advance(session, now=timezone.now())


@transaction.atomic
def stop_timing(*, session_id, actor=None, reason: str = "") -> WorkSession | None:
    """Close a session and its open interval. Idempotent.

    Closing an already-closed session is not an error — a client that retries
    its own stop, or whose ``sendBeacon`` raced the route cleanup, is behaving
    correctly and must not be told otherwise.

    Deliberately does **not** re-check edit permission: stopping is always safe,
    and refusing to stop a session because the annotator just lost the
    assignment would leave it open to be swept later instead of closed now.
    """
    locked = (
        WorkSession.objects.select_for_update()
        .select_related("task")
        .filter(pk=session_id)
        .first()
    )
    if locked is None:
        return None
    if actor is not None and locked.actor_id != getattr(actor, "id", None):
        raise TimingError("Not your session.", reason="forbidden")
    if not locked.is_open:
        return locked

    now = timezone.now()
    gained = credited_seconds(
        locked.last_heartbeat_at,
        now,
        max_interval=max_interval_seconds(),
        idle_timeout=server_idle_timeout_seconds(),
    )
    current = _open_interval(locked)
    if current is not None:
        end_at = (
            (locked.last_heartbeat_at + timedelta(seconds=gained))
            if (locked.last_heartbeat_at and gained > 0)
            else (locked.last_heartbeat_at or current.started_at)
        )
        _close_interval(
            current, at=end_at, reason=reason or WorkInterval.CloseReason.ENDED
        )
    locked.active_seconds = int(locked.active_seconds) + gained
    locked.ended_at = now
    locked.close_reason = (reason or WorkInterval.CloseReason.ENDED)[:32]
    locked.save(
        update_fields=["active_seconds", "ended_at", "close_reason", "updated_at"]
    )
    return locked


def stop_task_timing(task: AnnotationTask, *, actor=None, reason: str = "") -> int:
    """Close every open session on ``task``. Returns how many were closed.

    Used by Submit (close the current interval as part of handing the work over)
    and by assignment changes (the previous annotator's session must not keep
    accruing against work they no longer own).
    """
    query = WorkSession.objects.filter(task=task, ended_at__isnull=True)
    if actor is not None:
        query = query.filter(actor=actor)
    closed = 0
    for pk in list(query.values_list("pk", flat=True)):
        if stop_timing(session_id=pk, reason=reason) is not None:
            closed += 1
    return closed


def reconcile_abandoned(*, now=None, limit: int = 500) -> int:
    """Persist the cap on sessions whose client stopped heartbeating.

    Purely an optimisation and a tidiness measure: :func:`effective_interval_end`
    already applies the same cap on every read, so aggregates are correct
    whether or not this ever runs. Nothing here can change a reported total — it
    only writes down what reads were already computing.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=server_idle_timeout_seconds())
    stale = list(
        WorkSession.objects.filter(ended_at__isnull=True, last_heartbeat_at__lt=cutoff)
        .values_list("pk", flat=True)[:limit]
    )
    closed = 0
    for pk in stale:
        with transaction.atomic():
            session = (
                WorkSession.objects.select_for_update().filter(pk=pk).first()
            )
            if session is None or not session.is_open:
                continue
            anchor = session.last_heartbeat_at or session.started_at
            end_at = anchor + timedelta(seconds=abandon_grace_seconds())
            for interval in WorkInterval.objects.filter(
                session=session, ended_at__isnull=True
            ):
                _close_interval(
                    interval, at=end_at, reason=WorkInterval.CloseReason.EXPIRED
                )
            session.ended_at = end_at
            session.close_reason = WorkInterval.CloseReason.EXPIRED
            session.save(update_fields=["ended_at", "close_reason", "updated_at"])
            closed += 1
    return closed


# ---------------------------------------------------------------------------
# Aggregation — the union of intervals, never the sum of counters
# ---------------------------------------------------------------------------


def union_seconds(spans) -> int:
    """Seconds covered by ``spans``, counting overlap exactly once.

    This is what stops two tabs, two browsers, or two devices reporting double
    time. Spans are intervals on a line; the answer is the measure of their
    union, which is not the sum unless they happen to be disjoint.

    Callers must pass spans for **one annotator at a time**. Two people working
    on the same volume simultaneously really is two person-hours, and merging
    across actors would erase one of them.
    """
    cleaned = [(a, b) for a, b in spans if b > a]
    if not cleaned:
        return 0
    cleaned.sort(key=lambda span: span[0])
    total = 0.0
    start, end = cleaned[0]
    for next_start, next_end in cleaned[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += (end - start).total_seconds()
            start, end = next_start, next_end
    total += (end - start).total_seconds()
    return int(max(total, 0))


def _spans_by_actor(intervals, *, now=None):
    """Group intervals into ``{actor_id: [(start, end), ...]}``, capping open ones."""
    now = now or timezone.now()
    grace = abandon_grace_seconds()
    grouped: dict[int, list] = {}
    for interval in intervals:
        end = effective_interval_end(interval, now=now, grace=grace)
        if end <= interval.started_at:
            continue
        grouped.setdefault(interval.actor_id, []).append((interval.started_at, end))
    return grouped


def _interval_queryset(**filters):
    # ``select_related("session")`` is not optional: ``effective_interval_end``
    # reads ``session.last_heartbeat_at`` for every open interval, and without
    # it a report with N open intervals issues N extra queries.
    return (
        WorkInterval.objects.filter(**filters)
        .select_related("session")
        .only(
            "id", "actor_id", "started_at", "ended_at",
            "session__last_heartbeat_at", "session__started_at",
        )
    )


def _total_from(intervals, *, now=None) -> int:
    return sum(
        union_seconds(spans)
        for spans in _spans_by_actor(intervals, now=now).values()
    )


def task_time(task: AnnotationTask, *, actor=None, now=None) -> dict:
    """Cumulative measured time for one task.

    ``tracked=False`` means the volume is legacy-exempt, and the caller must
    render ``-``: not zero, not "0m", and not a blank that reads as zero.
    Submitting, reopening and submitting again all accumulate into ``seconds``,
    because nothing in this function looks at task status at all.
    """
    if not task_is_eligible(task):
        return {
            "tracked": False,
            "seconds": None,
            "display": "-",
            "eligibility": TimeTracking.LEGACY_EXEMPT,
        }
    filters = {"task": task}
    if actor is not None:
        filters["actor"] = actor
    seconds = _total_from(_interval_queryset(**filters), now=now)
    return {
        "tracked": True,
        "seconds": seconds,
        "display": format_duration(seconds),
        "eligibility": TimeTracking.ELIGIBLE,
    }


def task_time_map(tasks, *, now=None) -> dict[int, dict]:
    """:func:`task_time` for many tasks in **one** interval query.

    The serializer renders whole task lists, and calling ``task_time`` per row
    would be a query per task — the N+1 that
    ``test_time_tracking.QueryCountTests`` exists to prevent. Folding one query
    in Python is both correct and O(1) round trips.
    """
    tasks = list(tasks)
    if not tasks:
        return {}
    now = now or timezone.now()
    grace = abandon_grace_seconds()

    eligible_ids = [t.id for t in tasks if task_is_eligible(t)]
    spans: dict[int, dict[int, list]] = {}
    if eligible_ids:
        rows = WorkInterval.objects.filter(task_id__in=eligible_ids).values_list(
            "task_id", "actor_id", "started_at", "ended_at",
            "session__last_heartbeat_at",
        )
        for task_id, actor_id, started_at, ended_at, last_heartbeat in rows:
            if ended_at is None:
                anchor = last_heartbeat or started_at
                end = min(max(anchor, started_at) + timedelta(seconds=grace), now)
            else:
                end = ended_at
            if end <= started_at:
                continue
            spans.setdefault(task_id, {}).setdefault(actor_id, []).append(
                (started_at, end)
            )

    out: dict[int, dict] = {}
    for task in tasks:
        if not task_is_eligible(task):
            out[task.id] = {
                "tracked": False,
                "seconds": None,
                "display": "-",
                "eligibility": TimeTracking.LEGACY_EXEMPT,
            }
            continue
        seconds = sum(
            union_seconds(actor_spans)
            for actor_spans in spans.get(task.id, {}).values()
        )
        out[task.id] = {
            "tracked": True,
            "seconds": seconds,
            "display": format_duration(seconds),
            "eligibility": TimeTracking.ELIGIBLE,
        }
    return out


def volume_time(volume, *, actor=None, now=None) -> dict:
    """Cumulative measured time for every task on one volume."""
    if not volume_is_eligible(volume):
        return {
            "tracked": False,
            "seconds": None,
            "display": "-",
            "eligibility": TimeTracking.LEGACY_EXEMPT,
        }
    filters = {"volume": volume}
    if actor is not None:
        filters["actor"] = actor
    seconds = _total_from(_interval_queryset(**filters), now=now)
    return {
        "tracked": True,
        "seconds": seconds,
        "display": format_duration(seconds),
        "eligibility": TimeTracking.ELIGIBLE,
    }


def format_duration(seconds: int | None) -> str:
    """Compact duration: ``-``, ``0m``, ``37m``, ``2h 14m``, ``3d 4h``.

    Mirrored by ``formatDuration`` in ``frontend/src/time.ts``; the two are
    kept identical by :mod:`annotation.test_time_tracking` and
    ``frontend/src/time.test.ts`` sharing the same table of cases.

    ``None`` is the legacy-exempt "unknown", which is deliberately *not* the
    same string as a real zero.
    """
    if seconds is None:
        return "-"
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def annotator_time_report(actor, *, viewer=None, now=None) -> dict:
    """One annotator's measured time, as project → dataset → volume.

    Built from a **single** interval query plus one volume query, then folded in
    Python. The obvious shape — a queryset per project, then per dataset, then
    per volume — is the N+1 this deliberately avoids, and
    ``test_time_tracking.QueryCountTests`` holds it to that.

    Every level reports two things rather than one:

    * ``seconds`` — the union of this annotator's intervals below that node;
    * ``legacy_volumes`` — how many volumes underneath are legacy-exempt.

    Both are needed because they answer different questions. A project total of
    ``2h`` with three legacy volumes underneath is not "2h of work"; it is "2h
    that we measured, plus an unknown amount that we did not". Callers must
    surface the second half rather than presenting the first as complete.
    """
    from volumes.models import Volume

    now = now or timezone.now()
    intervals = (
        _interval_queryset(actor=actor)
        .annotate()
        .values_list("id", "volume_id", "started_at", "ended_at",
                     "session__last_heartbeat_at")
    )

    # Spans per volume for this one actor. Grouping by volume rather than by
    # actor is safe precisely because the query is already scoped to one actor.
    grace = abandon_grace_seconds()
    per_volume: dict[int, list] = {}
    for _id, volume_id, started_at, ended_at, last_heartbeat in intervals:
        if ended_at is None:
            anchor = last_heartbeat or started_at
            end = min(max(anchor, started_at) + timedelta(seconds=grace), now)
        else:
            end = ended_at
        if end <= started_at:
            continue
        per_volume.setdefault(volume_id, []).append((started_at, end))

    # Every volume this annotator holds or has worked on, so an eligible volume
    # with no sessions still reports 0m and a legacy one still reports '-'.
    volumes = (
        Volume.objects.filter(tasks__assigned_to=actor)
        .distinct()
        .select_related("project", "dataset")
    )
    known_ids = {v.id for v in volumes}
    missing = set(per_volume) - known_ids
    if missing:
        volumes = list(volumes) + list(
            Volume.objects.filter(id__in=missing).select_related("project", "dataset")
        )

    projects: dict[int, dict] = {}
    for volume in volumes:
        eligible = volume_is_eligible(volume)
        seconds = union_seconds(per_volume.get(volume.id, [])) if eligible else None
        project = volume.project
        dataset = volume.dataset
        dataset_key = dataset.id if dataset is not None else 0
        dataset_name = dataset.name if dataset is not None else "(no dataset)"

        project_row = projects.setdefault(
            project.id,
            {
                "project_id": project.id,
                "project_title": project.title,
                "seconds": 0,
                "legacy_volumes": 0,
                "datasets": {},
            },
        )
        dataset_row = project_row["datasets"].setdefault(
            dataset_key,
            {
                "dataset_id": dataset.id if dataset is not None else None,
                "dataset_name": dataset_name,
                "seconds": 0,
                "legacy_volumes": 0,
                "volumes": [],
            },
        )
        dataset_row["volumes"].append(
            {
                "volume_id": volume.id,
                "volume_name": volume.name,
                "tracked": eligible,
                "seconds": seconds,
                "display": format_duration(seconds),
            }
        )
        if eligible:
            dataset_row["seconds"] += seconds
            project_row["seconds"] += seconds
        else:
            dataset_row["legacy_volumes"] += 1
            project_row["legacy_volumes"] += 1

    def _finish(row, children_key, children):
        row[children_key] = children
        row["display"] = format_duration(row["seconds"])
        # "There is more here than this number says." The UI shows it as an
        # unobtrusive marker; without it a partly-legacy total reads as complete.
        row["has_legacy"] = row["legacy_volumes"] > 0
        return row

    project_rows = []
    total_seconds = 0
    total_legacy = 0
    for project_row in sorted(
        projects.values(), key=lambda r: (-r["seconds"], r["project_title"])
    ):
        dataset_rows = [
            _finish(
                dataset_row,
                "volumes",
                sorted(dataset_row["volumes"], key=lambda v: v["volume_name"]),
            )
            for dataset_row in sorted(
                project_row.pop("datasets").values(),
                key=lambda r: (-r["seconds"], r["dataset_name"]),
            )
        ]
        total_seconds += project_row["seconds"]
        total_legacy += project_row["legacy_volumes"]
        project_rows.append(_finish(project_row, "datasets", dataset_rows))

    return {
        "annotator": getattr(actor, "username", ""),
        "seconds": total_seconds,
        "display": format_duration(total_seconds),
        "legacy_volumes": total_legacy,
        "has_legacy": total_legacy > 0,
        "projects": project_rows,
    }
