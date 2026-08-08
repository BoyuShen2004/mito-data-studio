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
