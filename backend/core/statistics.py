"""Phase 6 — dashboard and statistics aggregates.

Every figure here is produced by a **grouped database query**, never by
iterating rows in Python. That is the whole design constraint (ADR-004 §3): a
dashboard that instantiates one model object per counted row stops working
exactly when a project gets big enough to be worth a dashboard.

Cross-app by nature — projects, tasks, submissions and reviews all
contribute — so it lives in ``core`` beside ``lifecycle.py`` rather than being
forced into one app.

On "time tracking"
------------------
mito records **timestamps**, not activity. There is no ``tracingTime``
accumulator and no instrumentation that could produce one, so everything
reported here is *elapsed wall-clock time between two recorded events*:

* time-to-submit  = submitted_at - assigned_at
* time-to-approve = approved_at  - submitted_at
* cycle time      = approved_at  - created_at

A task assigned on Friday and submitted on Monday shows three days regardless
of effort spent. Every key says ``elapsed``, never "time spent", so no consumer
can mistake one for the other. Real effort tracking needs the Phase 7 operation
log. See ADR-004 §2, conflict A.
"""

from __future__ import annotations

from django.conf import settings
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q

from core.choices import (
    ReviewDecision,
    TaskStatus,
)


def dashboards_enabled() -> bool:
    """Phase 6 endpoints are inert unless this is on."""
    return bool(getattr(settings, "FEATURE_DASHBOARDS", False))


def _seconds(delta) -> float | None:
    """A ``timedelta`` (or ``None``) as float seconds, for JSON and CSV."""
    return None if delta is None else round(delta.total_seconds(), 2)


def _elapsed(start: str, end: str):
    """``end - start`` computed by PostgreSQL, not by Python.

    Wrapped so the arithmetic stays in the database: pulling two timestamps per
    row back to subtract them here would defeat the point of aggregating.
    """
    return ExpressionWrapper(F(end) - F(start), output_field=DurationField())


# ---------------------------------------------------------------------------
# Task status distribution
# ---------------------------------------------------------------------------


def task_status_counts(project=None) -> dict[str, int]:
    """Tasks per status. **One** grouped query, whatever the row count.

    Replaces the per-row Python loop in
    ``projects.services.calculate_project_progress``, which transferred and
    instantiated every task row to produce six integers.

    Every status appears, zero-filled, so a caller rendering a chart never has
    to guess which bars are missing.
    """
    from annotation.models import AnnotationTask

    qs = AnnotationTask.objects.all()
    if project is not None:
        qs = qs.filter(project=project)

    counts = {status.value: 0 for status in TaskStatus}
    for row in qs.values("status").annotate(n=Count("id")):
        # A status not in the enum (legacy data) is reported rather than
        # dropped: silently discarding rows would make the totals lie.
        counts[row["status"]] = counts.get(row["status"], 0) + row["n"]
    return counts


# ---------------------------------------------------------------------------
# Elapsed durations  (NOT time spent — see the module docstring)
# ---------------------------------------------------------------------------


def elapsed_durations(project=None) -> dict[str, float | None]:
    """Mean elapsed seconds between recorded task events.

    One aggregate query. ``None`` means "no task has reached that stage yet",
    which is distinct from zero and must not be rendered as it.
    """
    from annotation.models import AnnotationTask

    qs = AnnotationTask.objects.all()
    if project is not None:
        qs = qs.filter(project=project)

    agg = qs.aggregate(
        to_submit=Avg(
            _elapsed("assigned_at", "submitted_at"),
            filter=Q(assigned_at__isnull=False, submitted_at__isnull=False),
        ),
        to_approve=Avg(
            _elapsed("submitted_at", "approved_at"),
            filter=Q(submitted_at__isnull=False, approved_at__isnull=False),
        ),
        cycle=Avg(
            _elapsed("created_at", "approved_at"),
            filter=Q(approved_at__isnull=False),
        ),
    )
    return {
        "mean_elapsed_to_submit_seconds": _seconds(agg["to_submit"]),
        "mean_elapsed_to_approve_seconds": _seconds(agg["to_approve"]),
        "mean_elapsed_cycle_seconds": _seconds(agg["cycle"]),
    }


# ---------------------------------------------------------------------------
# Review outcomes
# ---------------------------------------------------------------------------


def review_outcome_counts(project=None) -> dict[str, int]:
    """Review decisions by verdict — the rejection-rate input."""
    from annotation.models import ReviewRecord

    qs = ReviewRecord.objects.all()
    if project is not None:
        qs = qs.filter(task__project=project)

    counts = {d.value: 0 for d in ReviewDecision}
    for row in qs.values("decision").annotate(n=Count("id")):
        counts[row["decision"]] = counts.get(row["decision"], 0) + row["n"]
    return counts


def rejection_rate(outcomes: dict[str, int]) -> float | None:
    """Share of decisions that sent work back, 0..1.

    ``None`` when nothing has been reviewed — reporting 0.0 there would claim a
    perfect record that has not been earned.
    """
    total = sum(outcomes.values())
    if not total:
        return None
    sent_back = outcomes.get(ReviewDecision.REJECTED, 0) + outcomes.get(
        ReviewDecision.REVISION_REQUESTED, 0
    )
    return round(sent_back / total, 4)


# ---------------------------------------------------------------------------
# Project dashboard
# ---------------------------------------------------------------------------


def project_dashboard(project) -> dict:
    """Everything one project's dashboard needs, in a fixed number of queries.

    Constant in row count: the query count does not change between a project
    with ten tasks and one with a hundred thousand.
    """
    statuses = task_status_counts(project)
    outcomes = review_outcome_counts(project)
    total = sum(statuses.values())
    approved = statuses.get(TaskStatus.APPROVED, 0)

    return {
        "project": {
            "id": project.pk,
            "title": project.title,
            "paused": bool(getattr(project, "paused", False)),
            "priority": getattr(project, "priority", 0),
        },
        "tasks": {
            "total": total,
            "approved": approved,
            "percent_complete": round(100 * approved / total, 1) if total else 0.0,
            "status_counts": statuses,
        },
        "reviews": {
            "decision_counts": outcomes,
            "total": sum(outcomes.values()),
            "rejection_rate": rejection_rate(outcomes),
        },
        "elapsed": elapsed_durations(project),
    }


# ---------------------------------------------------------------------------
# Annotator statistics (the People-page data half — ADR-004 §2, conflict B)
# ---------------------------------------------------------------------------

# Hard ceiling. An unbounded people list is the other way a dashboard endpoint
# stops working at scale.
MAX_ANNOTATORS = 200


def annotator_statistics(*, project=None, limit: int = MAX_ANNOTATORS,
                         offset: int = 0) -> dict:
    """Per-annotator workload and outcomes, bounded and paginated.

    One grouped query, regardless of how many annotators or
    tasks exist — never one query per person.

    Complements ``accounts.services.annotator_task_counts`` rather than
    replacing it: that function answers "what does this one person have", this
    one answers "how does the whole roster compare", which it has no source for.
    """
    from annotation.models import AnnotationTask

    limit = max(1, min(int(limit), MAX_ANNOTATORS))
    offset = max(0, int(offset))

    tasks = AnnotationTask.objects.filter(assigned_to__isnull=False)
    if project is not None:
        tasks = tasks.filter(project=project)

    # One grouped query for task-side figures.
    by_task = {
        row["assigned_to"]: row
        for row in tasks.values("assigned_to", "assigned_to__username").annotate(
            assigned=Count("id"),
            approved=Count("id", filter=Q(status=TaskStatus.APPROVED)),
            submitted=Count("id", filter=Q(status=TaskStatus.SUBMITTED)),
            rejected=Count("id", filter=Q(status=TaskStatus.REJECTED)),
            mean_elapsed_to_submit=Avg(
                _elapsed("assigned_at", "submitted_at"),
                filter=Q(assigned_at__isnull=False, submitted_at__isnull=False),
            ),
        )
    }
    user_ids = sorted(by_task)
    total = len(user_ids)
    page = user_ids[offset : offset + limit]

    rows = []
    for uid in page:
        t = by_task.get(uid, {})
        rows.append({
            "user_id": uid,
            "username": t.get("assigned_to__username", ""),
            "tasks_assigned": t.get("assigned", 0),
            "tasks_approved": t.get("approved", 0),
            "tasks_submitted": t.get("submitted", 0),
            "tasks_rejected": t.get("rejected", 0),
            "mean_elapsed_to_submit_seconds": _seconds(
                t.get("mean_elapsed_to_submit")
            ),
        })
    return {
        "count": total,
        "limit": limit,
        "offset": offset,
        "results": rows,
    }


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

PROJECT_CSV_COLUMNS = [
    "project_id", "project_title", "paused", "tasks_total", "tasks_approved",
    "percent_complete", "reviews_total", "rejection_rate",
    "mean_elapsed_to_submit_seconds", "mean_elapsed_to_approve_seconds",
    "mean_elapsed_cycle_seconds",
]


def project_dashboard_csv_row(dashboard: dict) -> list:
    """Flatten a dashboard into one CSV row.

    Built from the *same* dict the JSON endpoint returns, so the two cannot
    report different numbers — a spreadsheet disagreeing with the screen is
    worse than either being slightly wrong.
    """
    p, t, r, e = (
        dashboard["project"], dashboard["tasks"],
        dashboard["reviews"], dashboard["elapsed"],
    )
    return [
        p["id"], p["title"], p["paused"], t["total"], t["approved"],
        t["percent_complete"], r["total"], r["rejection_rate"],
        e["mean_elapsed_to_submit_seconds"],
        e["mean_elapsed_to_approve_seconds"],
        e["mean_elapsed_cycle_seconds"],
    ]


# ---------------------------------------------------------------------------
# Delivery: milestones, throughput, and the attention queue
# ---------------------------------------------------------------------------
#
# Same constraint as everything above: one grouped query per figure, never a
# Python loop over task rows. The only Python iteration here is over already
# aggregated buckets (days, milestones, people), which is bounded by the size
# of the chart rather than by the size of the project.


def delivery_enabled() -> bool:
    """Are milestones and the delivery analytics turned on?"""
    return bool(getattr(settings, "FEATURE_MILESTONES", False))


def throughput_series(project=None, *, days: int = 30) -> dict:
    """Tasks approved per day over the trailing ``days`` window.

    **Zero-filled across the whole range.** A chart must never have to guess
    whether a missing day means "no work" or "no data" — the caller gets one
    entry per day either way.
    """
    from datetime import timedelta

    from django.db.models.functions import TruncDate
    from django.utils import timezone

    from annotation.models import AnnotationTask

    days = max(1, min(int(days), 365))
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)

    queryset = AnnotationTask.objects.filter(approved_at__date__gte=start)
    if project is not None:
        queryset = queryset.filter(project=project)

    counted = {
        row["day"]: row["n"]
        for row in queryset.annotate(day=TruncDate("approved_at"))
        .values("day")
        .annotate(n=Count("id"))
    }
    return {
        "start": start.isoformat(),
        "end": today.isoformat(),
        "points": [
            {
                "date": (start + timedelta(days=offset)).isoformat(),
                "approved": counted.get(start + timedelta(days=offset), 0),
            }
            for offset in range(days)
        ],
    }


def milestone_progress(milestone) -> dict:
    """One milestone's current standing. One grouped query.

    Progress is recomputed on every read rather than stored, so it can never
    drift from the tasks it describes — the same reason nothing else in this
    module caches a counter.
    """
    from datetime import date

    from annotation.models import AnnotationTask
    from core.choices import MilestoneMetric, VolumeStatus
    from volumes.models import Volume

    scoped_volume_ids = list(milestone.volumes.values_list("id", flat=True))

    if milestone.target_metric == MilestoneMetric.VOLUMES_COMPLETED:
        queryset = Volume.objects.filter(project=milestone.project)
        if scoped_volume_ids:
            queryset = queryset.filter(id__in=scoped_volume_ids)
        achieved = queryset.filter(status=VolumeStatus.COMPLETED).count()
    else:
        queryset = AnnotationTask.objects.filter(project=milestone.project)
        if scoped_volume_ids:
            queryset = queryset.filter(volume_id__in=scoped_volume_ids)
        achieved = queryset.filter(status=TaskStatus.APPROVED).count()

    target = int(milestone.target_value or 0)
    today = date.today()
    remaining = max(0, target - achieved)
    days_remaining = (milestone.due_on - today).days

    return {
        "id": milestone.pk,
        "name": milestone.name,
        "due_on": milestone.due_on.isoformat(),
        "status": milestone.status,
        "target_metric": milestone.target_metric,
        "target_value": target,
        "achieved": achieved,
        "remaining": remaining,
        # None rather than 0.0 for a target of zero: "no target set" and
        # "target met" must not render the same.
        "percent_complete": (
            round(100 * min(achieved, target) / target, 1) if target else None
        ),
        "days_remaining": days_remaining,
        "overdue": days_remaining < 0 and remaining > 0,
        "scoped_volumes": len(scoped_volume_ids),
    }


def burndown(milestone, *, days: int = 30) -> dict:
    """Remaining work against the straight line to the milestone's due date.

    The ideal line is deliberately naive — a straight descent from the target
    to zero across the window. It is a reference for reading the actual curve,
    not a forecast, and is never presented as a prediction.
    """
    from datetime import timedelta

    from django.utils import timezone

    progress = milestone_progress(milestone)
    series = throughput_series(milestone.project, days=days)
    target = progress["target_value"]

    today = timezone.localdate()
    points = series["points"]
    # Walk backwards from today's known remaining count so the actual line ends
    # at the same number the progress card shows. Reconstructing forwards from
    # a guessed starting point would let the two disagree.
    remaining = progress["remaining"]
    actual = []
    for point in reversed(points):
        actual.append({"date": point["date"], "remaining": remaining})
        remaining = min(target, remaining + point["approved"])
    actual.reverse()

    span = max(1, (milestone.due_on - today).days + len(points))
    ideal = []
    for index, point in enumerate(points):
        fraction = index / span
        ideal.append(
            {"date": point["date"], "remaining": max(0, round(target * (1 - fraction)))}
        )

    return {
        "milestone": progress,
        "actual": actual,
        "ideal": ideal,
    }


def annotator_productivity(project=None, *, limit: int = MAX_ANNOTATORS) -> dict:
    """Per-person output, including **real** measured annotation minutes.

    Two grouped queries: one over tasks, one over work intervals.

    ``annotated_seconds`` is ``None``, never ``0``, for a person whose work on
    this project happened entirely on ``TimeTracking.LEGACY_EXEMPT`` volumes —
    annotation there predates time tracking, so the real total is unknowable
    and reporting zero would be a lie about their effort. A person with
    eligible volumes and no recorded intervals correctly reports ``0``.
    """
    from django.db.models import Sum
    from django.db.models.functions import Coalesce

    from annotation.models import AnnotationTask, WorkInterval
    from core.choices import TimeTracking

    limit = max(1, min(int(limit), MAX_ANNOTATORS))

    tasks = AnnotationTask.objects.filter(assigned_to__isnull=False)
    intervals = WorkInterval.objects.filter(ended_at__isnull=False)
    eligible = WorkInterval.objects.filter(
        ended_at__isnull=False, volume__time_tracking=TimeTracking.ELIGIBLE
    )
    if project is not None:
        tasks = tasks.filter(project=project)
        intervals = intervals.filter(task__project=project)
        eligible = eligible.filter(task__project=project)

    by_task = {
        row["assigned_to"]: row
        for row in tasks.values("assigned_to", "assigned_to__username").annotate(
            assigned=Count("id"),
            approved=Count("id", filter=Q(status=TaskStatus.APPROVED)),
            submitted=Count("id", filter=Q(status=TaskStatus.SUBMITTED)),
            mean_rounds=Avg("submission_count"),
            mean_elapsed_to_submit=Avg(
                _elapsed("assigned_at", "submitted_at"),
                filter=Q(assigned_at__isnull=False, submitted_at__isnull=False),
            ),
        )
    }

    # Summed per person over closed intervals on time-tracked volumes only.
    # Overlapping intervals are summed rather than unioned here: this is a
    # comparative productivity readout, not the billed total that
    # ``annotation.timing`` computes with a proper union.
    measured = {
        row["actor"]: row["seconds"]
        for row in eligible.values("actor").annotate(
            seconds=Coalesce(
                Sum(_elapsed("started_at", "ended_at")), timedelta_zero()
            )
        )
    }
    # Who has any interval at all, so "no eligible volumes" can be told apart
    # from "eligible volumes but nothing recorded".
    has_any = set(intervals.values_list("actor", flat=True).distinct())
    has_eligible = set(eligible.values_list("actor", flat=True).distinct())

    rows = []
    for uid in sorted(by_task)[:limit]:
        row = by_task[uid]
        if uid in has_eligible:
            annotated = _seconds(measured.get(uid))
        elif uid in has_any:
            # Every recorded interval is on a legacy-exempt volume: unknowable.
            annotated = None
        else:
            annotated = 0.0
        rows.append(
            {
                "user_id": uid,
                "username": row.get("assigned_to__username", ""),
                "tasks_assigned": row.get("assigned", 0),
                "tasks_approved": row.get("approved", 0),
                "tasks_submitted": row.get("submitted", 0),
                "mean_review_rounds": (
                    round(row["mean_rounds"], 2) if row.get("mean_rounds") else None
                ),
                "mean_elapsed_to_submit_seconds": _seconds(
                    row.get("mean_elapsed_to_submit")
                ),
                "annotated_seconds": annotated,
            }
        )
    return {"count": len(by_task), "results": rows}


def timedelta_zero():
    """A zero duration typed for ``Coalesce`` over a ``DurationField``."""
    import datetime

    from django.db.models import Value

    return Value(datetime.timedelta(0), output_field=DurationField())


def attention_queue(project=None, *, soon_days: int = 3, stale_days: int = 3) -> dict:
    """What is late, nearly late, or has been waiting too long for a review.

    Three counted queries plus three small bounded lists. This is the "what
    should I do next" surface; it deliberately reports items, not just totals,
    because a count with no way to reach the work is not actionable.
    """
    from datetime import timedelta

    from django.utils import timezone

    from annotation.models import AnnotationSubmission, AnnotationTask
    from core.choices import SubmissionReviewStatus

    today = timezone.localdate()
    soon = today + timedelta(days=max(0, int(soon_days)))
    stale_before = timezone.now() - timedelta(days=max(0, int(stale_days)))

    # "Unfinished" is anything not yet approved. Rejected and revision-requested
    # work is still owed, so it belongs in the overdue list.
    unfinished = ~Q(status=TaskStatus.APPROVED)

    tasks = AnnotationTask.objects.filter(unfinished, deadline__isnull=False)
    submissions = AnnotationSubmission.objects.filter(
        review_status=SubmissionReviewStatus.PENDING,
        superseded_at__isnull=True,
        submitted_at__lt=stale_before,
    )
    if project is not None:
        tasks = tasks.filter(project=project)
        submissions = submissions.filter(task__project=project)

    overdue = tasks.filter(deadline__lt=today)
    due_soon = tasks.filter(deadline__gte=today, deadline__lte=soon)

    def _task_rows(queryset):
        return [
            {
                "id": row["id"],
                "volume": row["volume__name"],
                "project": row["project__title"],
                "deadline": row["deadline"].isoformat() if row["deadline"] else None,
                "status": row["status"],
                "assigned_to": row["assigned_to__username"],
            }
            for row in queryset.order_by("deadline", "id").values(
                "id", "volume__name", "project__title", "deadline", "status",
                "assigned_to__username",
            )[:50]
        ]

    return {
        "overdue": {"count": overdue.count(), "results": _task_rows(overdue)},
        "due_soon": {"count": due_soon.count(), "results": _task_rows(due_soon)},
        "stale_reviews": {
            "count": submissions.count(),
            "results": [
                {
                    "id": row["id"],
                    "task": row["task_id"],
                    "volume": row["task__volume__name"],
                    "annotator": row["annotator__username"],
                    "submitted_at": row["submitted_at"].isoformat(),
                }
                for row in submissions.order_by("submitted_at", "id").values(
                    "id", "task_id", "task__volume__name", "annotator__username",
                    "submitted_at",
                )[:50]
            ],
        },
        "thresholds": {"soon_days": soon_days, "stale_days": stale_days},
    }
