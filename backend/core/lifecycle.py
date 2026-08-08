"""Centralised New / To Proofread / Done lifecycle mapping.

The product exposes three high-level data-lifecycle views over the *existing*
domain statuses (project review gate, volume status, task status). Rather than
scattering ``if status in (...)`` conditions through views, admin, and React,
every caller classifies records through this one module.

The three buckets:

* **New** — registered but not yet in annotation/proofreading (pending review,
  approved-but-not-split, ingestion/processing/prediction/task-generation
  pending, failed-during-preparation, on hold).
* **To Proofread** — active annotation/proofreading work (available, assigned,
  claimed, in progress, submitted, under QA, awaiting review, revision
  requested, rejected).
* **Done** — accepted/completed work (approved, completed, delivered, published).

Classification is defined at the most granular level (the task) and rolled up to
volumes and projects. See ``progress/architecture.md`` for the rationale.
"""

from __future__ import annotations

from django.db import models

from .choices import ProjectStatus, TaskStatus, VolumeStatus


class Lifecycle(models.TextChoices):
    NEW = "new", "New"
    TO_PROOFREAD = "to_proofread", "To Proofread"
    DONE = "done", "Done"


# --- Task-level mapping (the granular anchor) ------------------------------
#
# A task only exists once work has been generated, so a task is never "New":
# it is either active work (To Proofread) or accepted (Done). "New" applies to
# projects/volumes that have not produced tasks yet.
TASK_STATUS_LIFECYCLE: dict[str, str] = {
    TaskStatus.UNASSIGNED: Lifecycle.TO_PROOFREAD,
    TaskStatus.ASSIGNED: Lifecycle.TO_PROOFREAD,
    TaskStatus.IN_PROGRESS: Lifecycle.TO_PROOFREAD,
    TaskStatus.SUBMITTED: Lifecycle.TO_PROOFREAD,
    TaskStatus.REVISION_REQUESTED: Lifecycle.TO_PROOFREAD,
    TaskStatus.REJECTED: Lifecycle.TO_PROOFREAD,
    TaskStatus.APPROVED: Lifecycle.DONE,
}

# --- Volume-level mapping --------------------------------------------------
VOLUME_STATUS_LIFECYCLE: dict[str, str] = {
    VolumeStatus.REGISTERED: Lifecycle.NEW,
    VolumeStatus.IN_ANNOTATION: Lifecycle.TO_PROOFREAD,
    VolumeStatus.COMPLETED: Lifecycle.DONE,
}

# Project statuses that are terminal enough to force a bucket regardless of
# task rollup. Everything else is derived from the review gate + task rollup.
PROJECT_STATUS_LIFECYCLE: dict[str, str] = {
    ProjectStatus.COMPLETED: Lifecycle.DONE,
    ProjectStatus.DELIVERED: Lifecycle.DONE,
}

# Reverse maps for building querysets ("tasks whose status is To Proofread").
TASK_STATUSES_BY_LIFECYCLE: dict[str, list[str]] = {
    Lifecycle.NEW: [],
    Lifecycle.TO_PROOFREAD: [
        s for s, lc in TASK_STATUS_LIFECYCLE.items() if lc == Lifecycle.TO_PROOFREAD
    ],
    Lifecycle.DONE: [
        s for s, lc in TASK_STATUS_LIFECYCLE.items() if lc == Lifecycle.DONE
    ],
}


def lifecycle_for_task_status(status: str) -> str:
    return TASK_STATUS_LIFECYCLE.get(status, Lifecycle.TO_PROOFREAD)


def lifecycle_for_volume_status(status: str) -> str:
    return VOLUME_STATUS_LIFECYCLE.get(status, Lifecycle.NEW)


def classify_task(task) -> str:
    """Return the lifecycle bucket for a single task."""
    return lifecycle_for_task_status(task.status)


def classify_volume(volume) -> str:
    """Return the lifecycle bucket for a single volume."""
    return lifecycle_for_volume_status(volume.status)


def classify_project(project) -> str:
    """Return the lifecycle bucket for a project, rolled up from its tasks.

    Rules, in order:

    1. A terminal project status (completed/delivered) is Done.
    2. A project that is not manager-reviewed, or has produced no tasks yet, is
       New — it has not entered annotation/proofreading.
    3. A project whose tasks are *all* approved is Done.
    4. Otherwise it has active work: To Proofread.
    """
    forced = PROJECT_STATUS_LIFECYCLE.get(project.status)
    if forced is not None:
        return forced

    if not project.manager_reviewed:
        return Lifecycle.NEW

    counts = _task_status_counts(project)
    total = sum(counts.values())
    if total == 0:
        return Lifecycle.NEW

    approved = counts.get(TaskStatus.APPROVED, 0)
    if approved == total:
        return Lifecycle.DONE
    return Lifecycle.TO_PROOFREAD


def _task_status_counts(project) -> dict[str, int]:
    """``{status: count}`` for a project's tasks (annotation-friendly to reuse)."""
    rows = project.tasks.values("status").order_by()
    counts: dict[str, int] = {}
    for row in rows:
        # ``values`` may already be aggregated by the caller; fall back to len.
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


def project_lifecycle_counts(projects) -> dict[str, int]:
    """Count how many of ``projects`` fall into each lifecycle bucket.

    ``projects`` is any iterable of ``Project`` instances. Returns a dict keyed
    by every :class:`Lifecycle` value so callers can render a stable dashboard.
    """
    counts = {lc.value: 0 for lc in Lifecycle}
    for project in projects:
        counts[classify_project(project)] += 1
    return counts


def filter_projects_by_lifecycle(queryset, lifecycle: str):
    """Return the subset of ``queryset`` whose projects are in ``lifecycle``.

    Classification depends on per-project task rollup, so this evaluates the
    queryset and filters in Python. It is intended for the modest project counts
    of the MVP, not million-row tables; callers that need SQL-level filtering
    should filter on the underlying statuses directly.
    """
    if lifecycle not in Lifecycle.values:
        raise ValueError(f"Unknown lifecycle: {lifecycle}")
    ids = [p.pk for p in queryset if classify_project(p) == lifecycle]
    return queryset.filter(pk__in=ids)
