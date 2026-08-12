"""Deterministic service functions for assignment, submission, and review."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from accounts.models import AnnotatorProfile
from accounts.teams import is_eligible_project_assignee
from core.utils import inspect_volume_voxel_size
from core.choices import (
    ACTIVE_TASK_STATUSES,
    HardCaseStatus,
    LabelType,
    QCStatus,
    ReviewDecision,
    SubmissionSource,
    SubmissionReviewStatus,
    TaskStatus,
)

from .models import (
    AssignmentWithdrawal,
    AnnotationSubmission,
    AnnotationTask,
    HardCase,
    HardCaseMessage,
    ReviewRecord,
)


def _serialized_volume_write(function):
    """Keep mask and lifecycle mutations for one volume in one write lane."""
    from functools import wraps

    @wraps(function)
    def guarded(volume, *args, **kwargs):
        from .label_paths import working_label_rel_path
        from .visualization.slice_io import resolve_path, serialized_file_write

        with serialized_file_write(resolve_path(working_label_rel_path(volume))):
            return function(volume, *args, **kwargs)

    return guarded


def _serialized_task_volume_write(function):
    """Task-shaped counterpart to :func:`_serialized_volume_write`."""
    from functools import wraps

    @wraps(function)
    def guarded(task, *args, **kwargs):
        from .label_paths import working_label_rel_path
        from .visualization.slice_io import resolve_path, serialized_file_write

        with serialized_file_write(resolve_path(working_label_rel_path(task.volume))):
            return function(task, *args, **kwargs)

    return guarded


# --- Assignment ------------------------------------------------------------

def assign_tasks_rule_based(project=None) -> dict:
    """Evenly distribute unassigned tasks across active annotators.

    Rules:
      * consider active annotators (``AnnotatorProfile.is_active_annotator``);
      * an annotator's load = tasks in ``assigned``/``in_progress`` status;
      * never exceed ``max_active_tasks``;
      * process tasks by priority desc, then created_at asc;
      * balance the work: each task goes to the eligible annotator with the
        fewest tasks assigned so far (existing load + this run), so volumes are
        spread out roughly evenly rather than piled onto one person.

    Returns a summary dict with the number assigned and per-annotator counts.
    """
    task_qs = AnnotationTask.objects.filter(status=TaskStatus.UNASSIGNED)
    if project is not None:
        task_qs = task_qs.filter(project=project)
    task_qs = task_qs.order_by("-priority", "created_at")

    # Build capacity + starting-load maps keyed by annotator user id.
    annotators = list(
        AnnotatorProfile.objects.filter(is_active_annotator=True).select_related("user")
    )
    users_by_id = {profile.user_id: profile.user for profile in annotators}
    capacity: dict[int, int] = {}
    load: dict[int, int] = {}
    for profile in annotators:
        active = AnnotationTask.objects.filter(
            assigned_to=profile.user, status__in=ACTIVE_TASK_STATUSES
        ).count()
        remaining = max(profile.max_active_tasks - active, 0)
        if remaining > 0:
            capacity[profile.user_id] = remaining
            load[profile.user_id] = active

    assigned_count = 0
    per_user: dict[int, int] = {}

    with transaction.atomic():
        for task in task_qs.select_for_update():
            if not any(rem > 0 for rem in capacity.values()):
                break
            available = [
                uid for uid, rem in capacity.items()
                if rem > 0 and is_eligible_project_assignee(
                    users_by_id[uid], task.project,
                )
            ]
            if not available:
                continue
            # Least-loaded annotator wins; ties broken by user id for stability.
            user_id = min(available, key=lambda uid: (load[uid], uid))
            task.assigned_to_id = user_id
            task.status = TaskStatus.ASSIGNED
            task.assigned_at = timezone.now()
            task.save(update_fields=["assigned_to", "status", "assigned_at"])

            capacity[user_id] -= 1
            load[user_id] += 1
            per_user[user_id] = per_user.get(user_id, 0) + 1
            assigned_count += 1

    return {
        "assigned": assigned_count,
        "per_user": per_user,
        "remaining_unassigned": task_qs.model.objects.filter(
            status=TaskStatus.UNASSIGNED,
            **({"project": project} if project is not None else {}),
        ).count(),
    }


def _plan_balanced_assignments(tasks, profiles) -> dict[int, int]:
    """Propose ``{task_id: annotator_user_id}`` without persisting anything.

    ``tasks`` is an ordered iterable of :class:`AnnotationTask` (highest
    priority first); ``profiles`` a list of active ``AnnotatorProfile``. Mirrors
    the balancing used by :func:`assign_tasks_rule_based` (respect each
    annotator's remaining capacity; least-loaded wins, ties by user id) but only
    computes a plan so a manager can review and edit it before it is committed.
    """
    capacity: dict[int, int] = {}
    load: dict[int, int] = {}
    for profile in profiles:
        active = AnnotationTask.objects.filter(
            assigned_to=profile.user, status__in=ACTIVE_TASK_STATUSES
        ).count()
        remaining = max(profile.max_active_tasks - active, 0)
        if remaining > 0:
            capacity[profile.user_id] = remaining
            load[profile.user_id] = active

    plan: dict[int, int] = {}
    for task in tasks:
        available = [uid for uid, rem in capacity.items() if rem > 0]
        if not available:
            break
        user_id = min(available, key=lambda uid: (load[uid], uid))
        plan[task.id] = user_id
        capacity[user_id] -= 1
        load[user_id] += 1
    return plan


def preview_assign_project(project) -> dict:
    """Build an editable assignment plan for a project without committing it.

    Ensures each volume has a whole-volume task, then proposes an annotator for
    every currently-unassigned task (balanced across active annotators). Tasks
    that are already assigned keep their current annotator. Nothing about who is
    assigned to what is saved — the manager reviews/edits the returned plan and
    commits it via :func:`apply_assignment_plan`.

    Returns ``{"reviewed", "created_tasks", "skipped_volumes", "proposed"}``
    where ``proposed`` maps ``task_id -> proposed_annotator_id`` (``None`` when
    no annotator has spare capacity).
    """
    if not project.manager_reviewed:
        return {
            "reviewed": False,
            "created_tasks": 0,
            "skipped_volumes": 0,
            "proposed": {},
            "detail": "Project must be reviewed by a manager before assignment.",
        }

    ensured = ensure_volume_tasks(project)
    unassigned = list(
        AnnotationTask.objects.filter(
            project=project, status=TaskStatus.UNASSIGNED
        ).order_by("-priority", "created_at")
    )
    from .scheduler import run_auto_fill, scheduler_enabled

    decision_id = None
    if scheduler_enabled():
        result = run_auto_fill(project=project, dry_run=True)
        proposed = {
            row["task_id"]: row["user_id"] for row in result.proposals
        }
        decision_id = result.decision_id
    else:
        profiles = list(
            AnnotatorProfile.objects.filter(
                is_active_annotator=True
            ).select_related("user")
        )
        profiles = [
            profile for profile in profiles
            if is_eligible_project_assignee(profile.user, project)
        ]
        proposed = _plan_balanced_assignments(unassigned, profiles)
    return {
        "reviewed": True,
        "created_tasks": ensured["created"],
        "skipped_volumes": ensured["skipped"],
        "proposed": proposed,
        "scheduler_decision_id": decision_id,
    }


def list_assignment_plan_rows(project) -> dict:
    """Ensure every volume has a whole-volume task and report the outcome,
    but — unlike :func:`preview_assign_project` — never propose annotators.

    Lets the assignment-plan UI show every volume that needs assigning (and
    let the manager start editing priority/difficulty/deadline/annotator
    right away) without first requiring a click on "Auto-fill balanced
    plan"; that button still owns proposing annotators, this just owns
    making sure a row exists to edit.
    """
    if not project.manager_reviewed:
        return {
            "reviewed": False,
            "created_tasks": 0,
            "skipped_volumes": 0,
            "detail": "Project must be reviewed by a manager before assignment.",
        }
    ensured = ensure_volume_tasks(project)
    return {
        "reviewed": True,
        "created_tasks": ensured["created"],
        "skipped_volumes": ensured["skipped"],
    }


# Task fields a manager may edit while curating an assignment plan.
PLAN_EDITABLE_FIELDS = ("priority", "difficulty", "instructions", "deadline")


def apply_assignment_plan(project, entries, *, annotators_by_id, actor=None) -> dict:
    """Commit a manager-edited assignment plan atomically.

    ``entries`` is a list of dicts, each carrying a ``task_id``, an optional
    ``annotator_id`` (``None``/absent unassigns), and any of the editable task
    fields in :data:`PLAN_EDITABLE_FIELDS`. ``annotators_by_id`` maps user id to
    a validated annotator ``User``. Every task must belong to ``project``; the
    whole plan is applied in one transaction so a bad entry rolls back the rest.

    Returns ``{"updated", "assigned", "remaining_unassigned"}``.
    """
    task_map = {t.id: t for t in project.tasks.select_related("assigned_to")}
    updated = 0

    with transaction.atomic():
        for entry in entries:
            task = task_map.get(entry["task_id"])
            if task is None:
                raise ValueError(
                    f"Task {entry['task_id']} does not belong to this project."
                )

            field_updates = [
                f for f in PLAN_EDITABLE_FIELDS if f in entry
            ]
            for field in field_updates:
                setattr(task, field, entry[field])
            if field_updates:
                task.save(update_fields=field_updates)

            if "annotator_id" in entry:
                annotator_id = entry["annotator_id"]
                annotator = (
                    annotators_by_id.get(annotator_id)
                    if annotator_id is not None
                    else None
                )
                assign_task_to_annotator(task, annotator=annotator, actor=actor)

            updated += 1

    remaining = project.tasks.filter(status=TaskStatus.UNASSIGNED).count()
    assigned = project.tasks.exclude(status=TaskStatus.UNASSIGNED).count()
    return {
        "updated": updated,
        "assigned": assigned,
        "remaining_unassigned": remaining,
    }


def create_whole_volume_task(volume):
    """Create one task spanning a volume's full extent, if it has none.

    Task bounds are 0-based and half-open on every axis. Thus `z_end` equals
    `shape_z` for a whole-volume task; it is not the inclusive final index.

    Returns the created :class:`AnnotationTask`, or ``None`` when the volume
    already has tasks (duplicate-safe) or has no detectable shape yet.
    ``deadline`` defaults to the project's own deadline — a manager can still
    override it per task in the assignment plan, but "same as the project"
    is the sane default rather than blank.
    """
    from volumes.services import ensure_volume_shape, infer_task_type

    if volume.tasks.exists():
        return None
    # Re-read the header before giving up. A volume registered while its
    # source was unreadable (a permission grant that came later, a mount that
    # was not up yet) has no shape recorded and would otherwise stay
    # unassignable forever, with re-registering as the only recovery.
    if not ensure_volume_shape(volume):
        return None
    return AnnotationTask.objects.create(
        project=volume.project,
        volume=volume,
        z_start=0,
        z_end=volume.shape_z,
        y_start=0,
        y_end=volume.shape_y or 0,
        x_start=0,
        x_end=volume.shape_x or 0,
        task_type=infer_task_type(volume.label_type),
        deadline=volume.project.deadline,
    )


def ensure_volume_tasks(project) -> dict:
    """Create one whole-volume annotation task per volume that has none.

    Auto-assignment works at the volume level: rather than splitting a volume
    into frames, each volume becomes a single task spanning its full extent, so
    a whole volume can be handed to one annotator. Volumes that already have
    tasks (e.g. a manager split them manually) are left untouched. Volumes
    whose shape still cannot be read — after :func:`create_whole_volume_task`
    has re-tried the header — are skipped, and each logs why.

    Returns ``{"created": n, "skipped": n}``.
    """
    created = 0
    skipped = 0
    existing_volume_ids = set(
        AnnotationTask.objects.filter(project=project).values_list("volume_id", flat=True)
    )
    for volume in project.volumes.exclude(id__in=existing_volume_ids):
        if create_whole_volume_task(volume) is not None:
            created += 1
        else:
            skipped += 1
    return {"created": created, "skipped": skipped}


def auto_assign_project(project, *, actor=None) -> dict:
    """Turn each volume into a task and distribute the volumes evenly.

    Requires the project to be manager-reviewed. Volumes with no task get one
    whole-volume task; then all unassigned tasks are balanced across active
    annotators. Returns a summary dict (``reviewed`` is ``False`` when blocked).
    """
    if not project.manager_reviewed:
        return {
            "reviewed": False,
            "assigned": 0,
            "created_tasks": 0,
            "skipped_volumes": 0,
            "per_user": {},
            "remaining_unassigned": 0,
            "detail": "Project must be reviewed by a manager before assignment.",
        }

    ensured = ensure_volume_tasks(project)
    from .scheduler import run_auto_fill, scheduler_enabled

    if scheduler_enabled():
        result = run_auto_fill(project=project, actor=actor)
        per_user: dict[int, int] = {}
        for row in result.proposals:
            uid = row["user_id"]
            per_user[uid] = per_user.get(uid, 0) + 1
        summary = {
            "assigned": result.assignments_made,
            "per_user": per_user,
            "remaining_unassigned": project.tasks.filter(
                assigned_to__isnull=True, status=TaskStatus.UNASSIGNED
            ).count(),
            "scheduler_decision_id": result.decision_id,
        }
    else:
        summary = assign_tasks_rule_based(project=project)
    summary["reviewed"] = True
    summary["created_tasks"] = ensured["created"]
    summary["skipped_volumes"] = ensured["skipped"]
    return summary


def assign_task_to_annotator(
    task: AnnotationTask, *, annotator, actor=None
) -> AnnotationTask:
    """Manually (re)assign a task to ``annotator`` (or unassign when ``None``).

    Updates the existing task in place. Reassignment keeps the same task row,
    so no duplicate annotation tasks are created.
    """
    with transaction.atomic():
        locked = AnnotationTask.objects.select_for_update().get(pk=task.pk)
        if annotator is None:
            locked.assigned_to = None
            locked.status = TaskStatus.UNASSIGNED
            locked.assigned_at = None
        else:
            locked.assigned_to = annotator
            locked.assigned_at = timezone.now()
            # Preserve genuine in-progress/review state during a manager transfer.
            if locked.status not in ACTIVE_TASK_STATUSES:
                locked.status = TaskStatus.ASSIGNED
        locked.save(update_fields=["assigned_to", "status", "assigned_at"])
    task.refresh_from_db()
    return task


def withdraw_project_assignments(
    project,
    *,
    team_name: str,
    retain_team=None,
    only_annotator_ids=None,
    reason: str = "Assignment withdrawn",
) -> dict:
    """Return affected tasks to the project's unassigned pool, with history.

    Before clearing each assignment, the volume's current working mask is
    promoted through the same official-label path used by in-app approval.
    ``retain_team`` keeps current assignees who also belong to a replacement
    working team. A durable :class:`AssignmentWithdrawal` row is what lets the
    former annotator see a cancelled Done item after ``assigned_to`` is clear.
    """
    retained_ids = set()
    if retain_team is not None:
        retained_ids = set(
            retain_team.memberships.values_list("user_id", flat=True)
        )
    tasks = project.tasks.filter(assigned_to__isnull=False)
    if retained_ids:
        tasks = tasks.exclude(assigned_to_id__in=retained_ids)
    if only_annotator_ids is not None:
        tasks = tasks.filter(assigned_to_id__in=set(only_annotator_ids))

    task_ids = []
    volume_ids = set()
    with transaction.atomic():
        locked = list(
            tasks.select_for_update().select_related("volume", "assigned_to")
        )
        for task in locked:
            if task.volume_id not in volume_ids:
                promote_working_label_to_official(task.volume)
                volume_ids.add(task.volume_id)
            AssignmentWithdrawal.objects.create(
                task=task,
                annotator=task.assigned_to,
                team_name=team_name,
                reason=reason,
            )
            _retire_submissions(task, reason="Assignment withdrawn.")
            task.assigned_to = None
            task.status = TaskStatus.UNASSIGNED
            task.assigned_at = None
            task.submitted_at = None
            task.approved_at = None
            task.annotation_locked = False
            task.submission_count = 0
            task.last_decision = ""
            task.last_decision_at = None
            task.last_decision_by = None
            task.last_decision_comments = ""
            task.last_decision_source = ""
            task.save(update_fields=[
                "assigned_to", "status", "assigned_at", "submitted_at",
                "approved_at", "annotation_locked", "submission_count",
                "last_decision", "last_decision_at", "last_decision_by",
                "last_decision_comments", "last_decision_source",
            ])
            task_ids.append(task.id)
    return {
        "withdrawn": len(task_ids),
        "task_ids": task_ids,
        "volume_count": len(volume_ids),
    }


# --- Submission + QC -------------------------------------------------------

def run_basic_qc(submission: AnnotationSubmission) -> dict:
    """Run the configured QC provider on a submission and persist the result.

    The checks themselves live behind the modular QC provider
    (``annotation.quality_control``); this function selects the provider, maps
    its structured report to a :class:`~core.choices.QCStatus`, and saves both.
    The default ``basic`` provider preserves the original file-level checks
    (linked to a task, present, non-empty, allowed extension).
    """
    from .quality_control.registry import get_qc_provider

    report = get_qc_provider().validate_submission(submission)

    if report.get("errors"):
        status = QCStatus.FAILED
    elif report.get("warnings"):
        status = QCStatus.WARNING
    else:
        status = QCStatus.PASSED

    submission.qc_status = status
    submission.qc_report = report
    submission.save(update_fields=["qc_status", "qc_report"])
    return report


def can_submit_task(user, task: AnnotationTask) -> bool:
    """May ``user`` hand ``task`` to a manager for review *right now*?

    The **single** source of truth for the Submit gate — serialized as
    ``can_submit`` and enforced by the submit endpoints, so the UI and the API
    cannot drift (they did: the frontend used to gate on a hard-coded
    ``["assigned", "in_progress", "revision_requested"]`` list, which made
    Submit vanish the moment a task went to ``submitted``).

    Two conditions, no status list:

    * the user can edit the task at all (manager, or the assigned annotator);
    * the task is not **locked** — i.e. no manager has approved it while
      declining "allow further annotation" (see :func:`approve_submission`).

    So an annotator may submit again after a previous submit, after a reject,
    after a revision request, and even after an approve that explicitly
    reopened the task.
    """
    return can_edit_task(user, task) and not task.annotation_locked


def can_annotate_task(user, task: AnnotationTask) -> bool:
    """May ``user`` *paint* on ``task``? Edit access, and not locked.

    ``can_edit_task`` answers "does this person have the role/assignment for
    this task"; this adds the manager's lock on top, and is what every
    working-copy mutation endpoint gates on (so an approved-and-locked task
    is 403 on the API, not merely hidden in the UI).
    """
    return can_edit_task(user, task) and not task.annotation_locked


def _supersede_submissions(
    task: AnnotationTask,
    *,
    keep=None,
    source: str | None = None,
    reason: str = "Replaced by a newer submission in the same channel.",
) -> int:
    """Void pending submissions without crossing channel boundaries.

    Submission history is now always durable: the UI must be able to explain
    which online/offline item won even when an older feature profile is used.
    A re-submit replaces only the pending row from its own ``source``.
    """
    return _retire_submissions(task, keep=keep, source=source, reason=reason)


def review_history_enabled() -> bool:
    """Compatibility flag retained for callers; history is always durable."""
    from django.conf import settings as _settings

    return bool(getattr(_settings, "FEATURE_REVIEW_HISTORY", False))


def _lock_task_for_submit(task: AnnotationTask) -> AnnotationTask:
    """Serialise submissions for one task. Returns the freshly-read row.

    Without this, two concurrent resubmits each mark "everything except mine"
    superseded against their own snapshot, neither sees the other's row, and
    the task ends up with several rows all claiming to be current. Measured:
    four concurrent resubmits produced four current submissions.

    Only the task row is locked, matching the lock ordering the claim and
    scheduler paths already use — no new lock class, so no new deadlock
    surface. Callers must already be inside a transaction.
    """
    return (
        AnnotationTask.objects.select_for_update().filter(pk=task.pk).first() or task
    )


def _retire_submissions(
    task: AnnotationTask,
    *,
    keep=None,
    source: str | None = None,
    reason: str = "Superseded.",
) -> int:
    """Mark prior submissions superseded instead of deleting them.

    Append-only: the row and, for uploads, the file are both retained. Keeping
    the file matters — a history row pointing at a deleted file is worse than
    no history, because it looks retrievable and is not.

    Idempotent: an already-superseded row keeps its original timestamp, so
    replaying a submit cannot rewrite when a round actually ended.

    Returns how many submissions were newly retired.
    """
    now = timezone.now()
    stale = task.submissions.filter(
        superseded_at__isnull=True,
        review_status=SubmissionReviewStatus.PENDING,
    )
    if source is not None:
        stale = stale.filter(source=source)
    if keep is not None:
        stale = stale.exclude(pk=keep.pk)
    # One UPDATE regardless of history depth — the count of prior rounds must
    # not turn a resubmit into a per-row loop.
    return stale.update(
        superseded_at=now,
        review_status=SubmissionReviewStatus.VOIDED,
        superseded_reason=reason,
    )


def current_submission(task: AnnotationTask, *, source: str | None = None):
    """The submission a reviewer should be looking at, or ``None``.

    One indexed lookup whether the task has one round or fifty. There can be
    one current row in each source channel, so callers that need a specific
    channel must pass ``source``.
    """
    rows = task.submissions.filter(
        superseded_at__isnull=True,
        review_status=SubmissionReviewStatus.PENDING,
    )
    if source is not None:
        rows = rows.filter(source=source)
    return rows.order_by("-submitted_at", "-id").first()


def submission_history(task: AnnotationTask, *, limit: int = 50):
    """Every submission for ``task``, newest first. Bounded by ``limit``."""
    return task.submissions.order_by("-submitted_at", "-id")[:limit]


def _set_task_status(task: AnnotationTask, target: str, *, extra_fields=()) -> None:
    """Move ``task`` to ``target``, checking the Phase 5 transition table first.

    Every status write in the review loop goes through here, so the table has
    exactly one enforcement point rather than one per verdict.

    The check reads the status **from the locked row**, not from the in-memory
    instance. A caller holding a `task` loaded minutes ago would otherwise be
    validated against a status the database no longer has — the check would
    pass on stale data and write anyway, which is precisely the class of bug a
    transition table exists to prevent. Locking also serialises two reviewers
    deciding the same task at once.
    """
    from .transitions import assert_transition

    with transaction.atomic():
        locked = (
            AnnotationTask.objects.select_for_update().filter(pk=task.pk).first()
        )
        current = locked.status if locked is not None else task.status
        assert_transition(current, target)
        task.status = target
        task.save(update_fields=["status", *extra_fields])


def _mark_submitted(task: AnnotationTask) -> None:
    """Common task bookkeeping for either submit path."""
    from .transitions import assert_transition

    assert_transition(task.status, TaskStatus.SUBMITTED)
    task.status = TaskStatus.SUBMITTED
    task.submitted_at = timezone.now()
    task.submission_count = (task.submission_count or 0) + 1
    task.save(update_fields=["status", "submitted_at", "submission_count"])


def submit_annotation(
    *, task: AnnotationTask, annotator, label_file, notes: str = ""
) -> AnnotationSubmission:
    """Record an annotator's uploaded-file submission, run QC, mark submitted.

    Replaces only the previous pending offline submission and refuses to run
    on a task the manager has locked. Approval installs this immutable upload
    as the official label and re-seeds the working copy from it.
    """
    if task.annotation_locked:
        raise ValueError(
            "This task was approved and closed for further annotation. "
            "Ask the manager to reopen it before submitting again."
        )
    with transaction.atomic():
        # Lock first: everything below decides what "the previous round" was,
        # and two concurrent resubmits must not each answer that from their own
        # snapshot.
        _lock_task_for_submit(task)
        # Captured before the new row exists so the chain links to the round it
        # actually replaced. Only when history is on: with the flag off the
        # previous row is about to be deleted, and pointing at it would be
        # noise that resolves to NULL anyway.
        previous = current_submission(task, source=SubmissionSource.UPLOAD)
        submission = AnnotationSubmission.objects.create(
            task=task, annotator=annotator, label_file=label_file, notes=notes,
            source=SubmissionSource.UPLOAD, supersedes=previous,
        )
        _supersede_submissions(
            task, keep=submission, source=SubmissionSource.UPLOAD
        )
        run_basic_qc(submission)
        _mark_submitted(task)
    return submission


def submit_inapp_annotation(
    *, task: AnnotationTask, annotator, notes: str = ""
) -> AnnotationSubmission:
    """Submit a task's in-app-edited *working* label copy for review.

    There is no user upload, but the service copies the current working TIFF
    into an immutable, submission-owned snapshot. The volume's official label
    remains untouched until that snapshot is approved.

    Submittable repeatedly: each call voids only the previous pending online
    row; an offline candidate remains independent. Raises ``ValueError`` if the task is locked,
    or if nothing has been painted/tracked yet (no working copy exists), so
    submitting is never a silent no-op.
    """
    from .label_paths import working_label_rel_path
    from .visualization.slice_io import resolve_path

    if task.annotation_locked:
        raise ValueError(
            "This task was approved and closed for further annotation. "
            "Ask the manager to reopen it before submitting again."
        )

    working_path = resolve_path(working_label_rel_path(task.volume))
    if not working_path.exists():
        raise ValueError(
            "Nothing has been annotated in-app for this task's volume yet — "
            "paint or track at least one slice before submitting."
        )

    from django.core.files import File

    with transaction.atomic():
        _lock_task_for_submit(task)
        previous = current_submission(task, source=SubmissionSource.INAPP)
        submission = AnnotationSubmission.objects.create(
            task=task, annotator=annotator, notes=notes,
            source=SubmissionSource.INAPP, supersedes=previous,
        )
        # A submission is a reviewable checkpoint, not a pointer to a mutable
        # working file. Saving an owned snapshot is what lets online and
        # offline channels remain independent while the annotator keeps paint-
        # ing or uploading another candidate.
        with working_path.open("rb") as handle:
            submission.label_file.save(
                f"online-snapshot-{submission.pk}.tif", File(handle), save=True
            )
        _supersede_submissions(
            task, keep=submission, source=SubmissionSource.INAPP
        )
        run_basic_qc(submission)
        _mark_submitted(task)
    return submission


def latest_submission_ids() -> list[int]:
    """Ids of the newest pending submission per task and source channel."""
    from django.db.models import Max

    return list(
        AnnotationSubmission.objects.filter(
            superseded_at__isnull=True,
            review_status=SubmissionReviewStatus.PENDING,
        )
        .values("task", "source")
        .annotate(newest=Max("id"))
        .values_list("newest", flat=True)
    )


# --- Review ----------------------------------------------------------------

def review_submission(
    *,
    submission: AnnotationSubmission,
    reviewer,
    decision: str,
    comments: str = "",
    allow_further_annotation: bool = False,
) -> ReviewRecord:
    """Record a review decision and apply the resulting task-state change.

    ``allow_further_annotation`` only means anything for ``approved`` — it is
    the manager's "the annotator may keep working on this" switch (see
    :func:`approve_submission`); reject/revision always reopen the task.
    """
    if decision == ReviewDecision.APPROVED:
        return approve_submission(
            submission,
            reviewer=reviewer,
            comments=comments,
            allow_further_annotation=allow_further_annotation,
        )
    if decision == ReviewDecision.REJECTED:
        return reject_submission(submission, reviewer=reviewer, comments=comments)
    if decision == ReviewDecision.REVISION_REQUESTED:
        return request_revision(submission, reviewer=reviewer, comments=comments)
    raise ValueError(f"Unknown review decision: {decision}")


def _record_review(submission, reviewer, decision, comments) -> ReviewRecord:
    """Log the decision and denormalize it onto the task.

    ``task`` and ``source`` are stored directly on the durable review record,
    so the winning channel remains explicit after later rounds. The task copy
    of the last decision lets list surfaces render without a join.
    """
    task = submission.task
    review = ReviewRecord.objects.create(
        submission=submission,
        task=task,
        reviewer=reviewer,
        decision=decision,
        source=submission.source,
        comments=comments,
    )
    task.last_decision = decision
    task.last_decision_at = review.reviewed_at
    task.last_decision_by = reviewer
    task.last_decision_comments = comments
    task.last_decision_source = submission.source
    task.save(
        update_fields=[
            "last_decision",
            "last_decision_at",
            "last_decision_by",
            "last_decision_comments",
            "last_decision_source",
        ]
    )
    # Best-effort, outside the review's own correctness: an audit failure must
    # never turn a recorded verdict into a 500.
    from accounts.audit import record_audit
    from core.choices import AuditVerb

    record_audit(
        reviewer, AuditVerb.REVIEW_RECORDED, target=task,
        review_id=review.pk, submission_id=submission.pk, decision=str(decision),
    )
    return review


def approve_submission(
    submission, *, reviewer=None, comments="", allow_further_annotation: bool = False
) -> ReviewRecord:
    """Approve exactly one online/offline submission and install its labels.

    Both channels carry an immutable file by review time: uploads carry the
    user's file, while in-app submits snapshot the working TIFF. Approval
    copies the winner to an app-owned official path, repoints the volume, then
    discards and re-seeds the working draft from that official result. Pending
    submissions in the other channel are voided, never silently overwritten.

    ``allow_further_annotation`` is the manager's switch on the review form:

    * **False** (the default — approve means "done") sets
      ``task.annotation_locked``, so painting and submitting both 403 and the
      editor drops to View-only.
    * **True** leaves the task unlocked: the promotion still happens, and the
      annotator may keep editing and submit again, which puts the task back
      into ``submitted`` for another review round.

    """
    with transaction.atomic():
        # Refresh under locks: two reviewers can open the Online and Offline
        # rows concurrently, but only the first committed approve may win.
        #
        # The task is locked *first*, and it is the only lock the loser ever
        # waits on. Taking the submission first deadlocked instead: competing
        # approves grab different submission rows without conflicting, then
        # both reach for the one task they share while each already holds a
        # row the other needs. Locking the shared parent up front makes the
        # order task -> submission -> volume for every approve, so the loser
        # simply queues and then finds its own row no longer pending.
        AnnotationTask.objects.select_for_update().get(pk=submission.task_id)
        # ``of=("self",)`` keeps select_related from re-locking the joined task
        # and volume rows as a side effect, which would reintroduce an implicit
        # ordering this function does not control.
        locked = AnnotationSubmission.objects.select_for_update(
            of=("self",)
        ).select_related("task__volume").get(pk=submission.pk)
        if locked.review_status != SubmissionReviewStatus.PENDING:
            raise ValueError("This submission is no longer pending review.")

        # Validate before copying any files. A stale review request must never
        # mutate the official label and fail only afterward.
        from .transitions import assert_transition
        assert_transition(locked.task.status, TaskStatus.APPROVED)

        _install_submission_as_official(locked)
        review = _record_review(
            locked, reviewer, ReviewDecision.APPROVED, comments
        )
        task = locked.task
        locked.review_status = SubmissionReviewStatus.APPROVED
        locked.save(update_fields=["review_status"])
        loser = "Offline" if locked.source == SubmissionSource.INAPP else "Online"
        winner = "Online" if locked.source == SubmissionSource.INAPP else "Offline"
        _retire_submissions(
            task,
            keep=locked,
            reason=f"{loser} submission voided — superseded by {winner} approve.",
        )
        task.approved_at = timezone.now()
        task.annotation_locked = not allow_further_annotation
        _set_task_status(
            task, TaskStatus.APPROVED,
            extra_fields=("approved_at", "annotation_locked"),
        )
        return review


def _install_submission_as_official(submission: AnnotationSubmission) -> None:
    """Install one reviewed file as official, then rebuild the working draft.

    The registered image and region-mask sources are never written. The
    accepted label is copied into the app-owned dataset folder, validated
    against the registered volume shape, and recorded as both the official
    label and the source future working resets fork from.
    """
    import os
    import shutil
    import tempfile

    from .label_paths import approved_label_rel_path
    from .visualization.slice_io import _open_volume, resolve_path

    if not submission.label_file:
        raise ValueError("The submission has no label snapshot to approve.")
    source = resolve_path(submission.label_file.name)
    if not source.exists():
        raise ValueError("The submitted label file is missing from storage.")

    try:
        opened = _open_volume(source)
    except Exception as exc:
        raise ValueError("The submitted label file could not be read.") from exc
    try:
        actual_shape = tuple(int(value) for value in opened.shape)
    finally:
        close = getattr(opened, "close", None)
        if close is not None:
            close()
    volume = submission.task.volume
    expected_shape = (volume.shape_z, volume.shape_y, volume.shape_x)
    if all(value is not None for value in expected_shape) and actual_shape != expected_shape:
        raise ValueError(
            f"Submitted label shape {actual_shape} does not match volume shape {expected_shape}."
        )

    official_rel = approved_label_rel_path(volume, submission)
    official = resolve_path(official_rel)
    official.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=official.parent, delete=False) as tmp:
        temporary = tmp.name
        with source.open("rb") as handle:
            shutil.copyfileobj(handle, tmp)
    try:
        os.replace(temporary, official)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)

    metadata = dict(volume.metadata or {})
    metadata[REGISTERED_LABEL_KEY] = official_rel
    volume.metadata = metadata
    volume.label_path = official_rel
    volume.label_file = None
    if volume.label_type == LabelType.NONE:
        volume.label_type = LabelType.PARTIAL
    volume.save(update_fields=["metadata", "label_path", "label_file", "label_type"])

    # This deletes only the app-owned working draft and its derived prompt /
    # lifecycle state, then seeds a fresh working TIFF from the new official.
    reset_working_labels_to_registered(submission.task)


def set_task_annotation_lock(task: AnnotationTask, *, locked: bool) -> AnnotationTask:
    """Flip a task's annotation lock after the fact (manager control).

    Lets a manager reopen a task they closed on approve — or close one they
    reopened — without inventing a second review round.
    """
    task.annotation_locked = bool(locked)
    task.save(update_fields=["annotation_locked"])
    return task


def promote_working_label_to_official(volume) -> None:
    """Repoint ``volume``'s official label at its current working copy.

    Shared by in-app approval and manager assignment withdrawal. If the
    working copy has vanished or was never created, this is a no-op: there is
    no staged mask to adopt, and the previously official label remains intact.
    External source labels are never overwritten; the volume is repointed to
    the app-owned working TIFF, matching the established approval semantics.
    """
    from .label_paths import working_label_rel_path
    from .visualization.slice_io import resolve_path

    rel = working_label_rel_path(volume)
    if not resolve_path(rel).exists():
        return

    update_fields = _repoint_label(volume, rel)
    if volume.label_type == LabelType.NONE:
        volume.label_type = LabelType.PARTIAL
        update_fields.append("label_type")
    if update_fields:
        volume.save(update_fields=update_fields)


# Backwards-compatible private name retained for callers/tests that predate
# team-withdrawal reuse of the promotion service.
_promote_working_label_to_official = promote_working_label_to_official


def _return_to_annotator(
    submission, *, reviewer, decision: str, review_status: str, task_status: str,
    comments: str,
) -> ReviewRecord:
    """Shared body of reject / request-revision: hand the work back, promote
    nothing, and leave the *other* channel's pending row alone.

    Locked in the same order as :func:`approve_submission` (task, then
    submission) and re-read from the locked row. A reviewer can hold a form
    opened before the sibling channel was approved; without the refresh this
    would write a decision onto an already-voided submission and stamp the
    task's ``last_decision`` with a verdict that never applied.

    The task drops back to ``task_status`` only once no pending round is left
    anywhere: while the other channel is still awaiting review the task stays
    submitted, and only ``annotation_locked`` is cleared.
    """
    with transaction.atomic():
        AnnotationTask.objects.select_for_update().get(pk=submission.task_id)
        locked = AnnotationSubmission.objects.select_for_update(
            of=("self",)
        ).select_related("task").get(pk=submission.pk)
        if locked.review_status != SubmissionReviewStatus.PENDING:
            raise ValueError("This submission is no longer pending review.")

        review = _record_review(locked, reviewer, decision, comments)
        locked.review_status = review_status
        locked.save(update_fields=["review_status"])
        # Keep the caller's handle usable, as the pre-lock version did.
        submission.review_status = review_status
        task = locked.task
        task.annotation_locked = False
        if current_submission(task) is None:
            _set_task_status(task, task_status, extra_fields=("annotation_locked",))
        else:
            task.save(update_fields=["annotation_locked"])
        return review


def reject_submission(submission, *, reviewer=None, comments="") -> ReviewRecord:
    """Reject: the task goes back to the annotator, nothing is promoted.

    ``rejected`` and ``revision_requested`` stay distinct *statuses* (the
    manager's verdict is worth showing), but neither gates work: the annotator
    keeps Annotate + Submit because :func:`can_submit_task` keys off
    ``annotation_locked`` alone, which a rejection explicitly clears.
    """
    return _return_to_annotator(
        submission,
        reviewer=reviewer,
        decision=ReviewDecision.REJECTED,
        review_status=SubmissionReviewStatus.REJECTED,
        task_status=TaskStatus.REJECTED,
        comments=comments,
    )


def request_revision(submission, *, reviewer=None, comments="") -> ReviewRecord:
    """Request revision — same "back to the annotator, unlocked" shape as
    :func:`reject_submission`, with a softer verdict on the badge."""
    return _return_to_annotator(
        submission,
        reviewer=reviewer,
        decision=ReviewDecision.REVISION_REQUESTED,
        review_status=SubmissionReviewStatus.REVISION_REQUESTED,
        task_status=TaskStatus.REVISION_REQUESTED,
        comments=comments,
    )


# --- Role-based view/edit access -------------------------------------------

def can_edit_task(user, task) -> bool:
    """May ``user`` *annotate* ``task``? Managers, or the assigned annotator.

    Requesters (Institutions) can never edit — enforced here so the API and the
    provider launch info agree with the UI.
    """
    from accounts.roles import is_annotator, is_manager

    if is_manager(user):
        return True
    uid = getattr(user, "id", None)
    return is_annotator(user) and task.assigned_to_id == uid


def is_project_member(user, project) -> bool:
    """Is ``user`` part of ``project``'s working group?

    Membership is project-centric (the design the People + Hard Cases
    surfaces are built on): the manager(s), the requester who owns it, and
    **every annotator holding a task on it** — including annotators whose own
    task is a *different* volume. That last part is the point: a hard case
    someone flags is for the team to look at, and teammates read it through
    the ordinary authed viewer rather than a second permission system.

    Phase 1 adds a *third* way in: a team the project has been granted to.
    Team access is strictly additive — it can only widen the set, never narrow
    it — and is consulted only when ``FEATURE_TEAMS`` is on, so with the flag
    off this function behaves exactly as it always did.
    """
    from accounts.roles import is_manager

    if project is None:
        return False
    if is_manager(user):
        return True
    uid = getattr(user, "id", None)
    if uid is None:
        return False
    if project.created_by_id == uid:
        return True
    if project.memberships.filter(user_id=uid).exists():
        return True
    if project.tasks.filter(assigned_to_id=uid).exists():
        return True

    from accounts.teams import has_project_team_access, teams_enabled

    return teams_enabled() and has_project_team_access(user, project)


def can_view_task(user, task) -> bool:
    """May ``user`` *view* ``task``? Editors, the assignee, or any project member.

    Requester + annotator both look at the same underlying task labels here, so
    progress monitoring reads one shared source of truth.
    """
    from accounts.roles import is_manager

    if is_manager(user) or can_edit_task(user, task):
        return True
    uid = getattr(user, "id", None)
    if task.assigned_to_id == uid:
        return True
    return is_project_member(user, task.project)


def can_view_volume(user, volume) -> bool:
    """May ``user`` view a whole volume? Managers, or any member of its project
    (the requester who owns it, or an annotator with a task on the project)."""
    from accounts.roles import is_manager

    if is_manager(user):
        return True
    uid = getattr(user, "id", None)
    if volume.tasks.filter(assigned_to_id=uid).exists():
        return True
    return is_project_member(user, volume.project)


# --- Provider-backed task helpers ------------------------------------------

def get_visualization_state(volume_or_task) -> dict:
    """Return the viewer URL + state for a volume or task."""
    from .visualization.registry import get_visualization_provider

    provider = get_visualization_provider()
    state = provider.get_view_state(volume_or_task)
    state["url"] = provider.get_view_url(volume_or_task)
    return state


# --- Fork-aware SAM2 tracking (persistence) --------------------------------
#
# Everything below writes only the *working* label copy
# (``label_paths.working_label_rel_path`` — nested under
# ``labels/<project>/<dataset>/`` so the on-disk layout matches the project →
# dataset → volume hierarchy the frontend shows). It never touches
# ``volume.label_path``/``label_file`` (the *official*, approved label) —
# that only changes in ``approve_submission``, once a manager approves an
# in-app submission. Before that point, the working copy is purely a staging
# area: the annotator (or a manager editing directly) can paint/track freely
# without affecting what any other viewer sees as "the" label.

def _load_or_init_label(volume, shape):
    """Load the working instance-label array, seed from official, or start empty.

    Precedence is deliberate: **working copy, then official label, then zeros.**

    Once a working copy exists it is the annotator's current source of truth.
    Falling back to the last-approved official label at that point would make a
    later whole-volume operation — notably :func:`track_task_fork`, which hands
    its result to :func:`_save_label_volume` and so rewrites the *entire*
    working copy — replace already-saved draft edits with stale approved
    pixels. That silently reverted an annotator's work between approvals.

    Fixing it here rather than in each caller means every present and future
    whole-volume operation inherits the correct behaviour.
    """
    import numpy as np

    from .label_paths import working_label_rel_path
    from .visualization.slice_io import resolve_path

    working_path = resolve_path(working_label_rel_path(volume))
    if working_path.exists():
        import tifffile

        try:
            arr = np.asarray(tifffile.imread(str(working_path)))
        except Exception:
            # A corrupt working copy means "fall back", never "rewrite it here".
            arr = None
        if arr is not None and arr.shape == tuple(shape):
            return arr.astype(np.int32)

    if volume.label_location:
        path = resolve_path(volume.label_location)
        if path.exists():
            import tifffile

            from .visualization.hdf5_io import is_hdf5_path, open_hdf5_volume
            from .visualization.nifti_io import is_nifti_path, open_nifti_volume

            try:
                if is_hdf5_path(path):
                    view = open_hdf5_volume(path)
                    try:
                        arr = np.asarray(view)
                    finally:
                        view.close()
                elif is_nifti_path(path):
                    arr = np.asarray(open_nifti_volume(path))
                else:
                    arr = np.asarray(tifffile.imread(str(path)))
            except Exception:
                # The official label may be registered by reference to
                # someone else's tree — never quarantine/rewrite it here;
                # a corrupt/unreadable seed just means "start empty".
                arr = None
            if arr is not None and arr.shape == tuple(shape):
                return arr.astype(np.int32)
    return np.zeros(shape, dtype=np.int32)


def _save_label_volume(volume, label_mask) -> str:
    """Write the working instance labels under MITO_DATA_ROOT; return rel path.

    Always writes to the app-owned working-copy path
    (:func:`annotation.label_paths.working_label_rel_path`) — **never** back
    onto whatever ``volume.label_location`` currently resolves to. That
    location can be a file registered *by reference* (a path into someone
    else's data, e.g. an externally produced prediction/consensus volume)
    which this app does not own and must not mutate in place, or it can be
    the volume's own *official* (approved) label, which must not change
    until a manager approves the new submission (see ``approve_submission``).
    The first in-app edit "forks" a mutable working copy here;
    :func:`_load_or_init_label` seeds that copy from the current official
    label the first time it's read, so nothing is lost — it's just staged,
    not yet promoted.

    Writes a memory-mappable TIFF (via :func:`slice_io._create_label_memmap`)
    so paint strokes can keep using cheap ``memmap`` afterwards.
    """
    import numpy as np

    from .label_paths import working_label_rel_path
    from .visualization.slice_io import resolve_path

    rel = working_label_rel_path(volume)
    path = resolve_path(rel)
    from .visualization import slice_io

    # Whole-volume tools must obey the same lifecycle lock as a brush Save.
    # Check the existing mmap in bounded z slabs before replacing any bytes.
    if path.exists():
        existing = slice_io._open_volume(path)
        _assert_verified_volume_unchanged(volume, existing, label_mask)

    # Drop any open writable memmap *before* replacing the file on disk —
    # otherwise Windows/NFS can keep a stale handle, and a crash mid-imwrite
    # has left us with a non-memmapable TIFF (see open_label_volume_writable).
    # Scoped to this file: the handle that must be released is this one, and
    # wiping every other volume's caches only made unrelated readers slow.
    slice_io.drop_file(path)
    arr = np.asarray(label_mask, dtype=np.uint16)
    mm = slice_io._create_label_memmap(path, tuple(arr.shape), seed=arr)
    del mm
    slice_io.drop_file(path)
    # Every slice may have changed — there is no single slice to fold into the
    # Labels summary, so drop it and let the next read rebuild.
    from .cellable_port.labels_3d import forget_summary

    forget_summary(path)
    slice_io.set_label_max_id(path, int(arr.max()) if arr.size else 0)
    return rel


def _repoint_label(volume, rel: str) -> list[str]:
    """Make ``volume.label_location`` resolve to ``rel`` from now on — i.e.
    promote it to the volume's *official* label. Called only from
    ``approve_submission``, once a manager approves an in-app submission;
    never from the paint/track paths (those only ever touch the working
    copy — see the module note above).

    ``label_location`` prefers ``label_file`` over ``label_path`` (see the
    model), so if an uploaded ``label_file`` is still set it must be cleared
    here too — otherwise every future read would keep resolving back to
    whatever it pointed at *before* this promotion and silently ignore it.

    Promotion is also the one moment the *registered* label stops being
    reachable through ``label_location``, so it is recorded here first. Without
    that, "reset my working labels to what was registered" would mean "reset to
    the last approved state" on every approved volume — see
    :func:`registered_label_location`.
    """
    update_fields = []
    metadata = dict(volume.metadata or {})
    if REGISTERED_LABEL_KEY not in metadata:
        metadata[REGISTERED_LABEL_KEY] = volume.label_location or ""
        volume.metadata = metadata
        update_fields.append("metadata")
    if volume.label_path != rel:
        volume.label_path = rel
        update_fields.append("label_path")
    if volume.label_file:
        volume.label_file = None
        update_fields.append("label_file")
    return update_fields


REGISTERED_LABEL_KEY = "registered_label_location"


def registered_label_location(volume) -> str:
    """Where this volume's *registered* label mask lives — the immutable source
    a working copy is seeded from, and what Reset restores.

    Three cases, in order:

    * an explicit record written at promotion time (see :func:`_repoint_label`);
    * otherwise ``label_location``, unless it now points at the working copy —
      which is what promotion makes it do, and resetting a file to itself is a
      no-op dressed up as an action;
    * otherwise ``""``, meaning "registered with no label at all", which resets
      to an empty mask. That is the correct answer for a volume registered
      image-only, and is deliberately not an error.

    Volumes approved *before* this record existed have genuinely lost their
    original registration from the database; for those the second case gives the
    last approved label, which is the closest honest answer available.
    """
    from .label_paths import working_label_rel_path
    from .visualization.slice_io import resolve_path

    recorded = (volume.metadata or {}).get(REGISTERED_LABEL_KEY)
    if isinstance(recorded, str):
        return recorded
    current = volume.label_location or ""
    if not current:
        return ""
    if resolve_path(current) == resolve_path(working_label_rel_path(volume)):
        return ""
    return current


TRACKING_PROMPTS_KEY = "tracking_prompts"
TRACKING_PROMPTS_VERSION = 1
TRACKING_PENDING_KEY = "tracking_pending_review"


def _tracking_seed_range(subclasses: list[dict]) -> list[int]:
    """Authoritative parent range from committed child seed planes only."""
    zs = [
        int(seed["z"])
        for child in subclasses
        for seed in child.get("seeds", [])
        if "z" in seed
    ]
    return [min(zs), max(zs)] if zs else [0, 0]


def tracking_pending_review(task: AnnotationTask) -> dict | None:
    pending = (task.volume.metadata or {}).get(TRACKING_PENDING_KEY)
    if not isinstance(pending, dict):
        return None
    return {
        "parent_ids": [int(value) for value in pending.get("parent_ids", [])],
        "status": "pending_review",
    }


def list_tracking_prompts(task: AnnotationTask) -> list[dict]:
    """Return the durable Track draft queue for this task's volume."""
    payload = (task.volume.metadata or {}).get(TRACKING_PROMPTS_KEY, {})
    if isinstance(payload, list):  # tolerate a short-lived pre-version schema
        return payload
    if not isinstance(payload, dict):
        return []
    return list(payload.get("items", []))


def _store_tracking_prompts(volume, prompts: list[dict]) -> None:
    volume.metadata = {
        **(volume.metadata or {}),
        TRACKING_PROMPTS_KEY: {
            "version": TRACKING_PROMPTS_VERSION,
            "items": prompts,
        },
    }
    volume.save(update_fields=["metadata"])


def upsert_tracking_prompt(task: AnnotationTask, prompt: dict) -> dict:
    """Create/update one RLE-backed draft without touching label pixels."""
    parent_id = int(prompt.get("parent_id", 0))
    if parent_id < 1:
        raise ValueError("parent_id must be a positive label id")
    subclasses = list(prompt.get("subclasses", []))
    normalized = {
        "parent_id": parent_id,
        "subclasses": subclasses,
        "z_range": _tracking_seed_range(subclasses),
        "status": str(prompt.get("status", "draft")),
        "note": str(prompt.get("note", "")),
    }
    if len(normalized["z_range"]) != 2:
        raise ValueError("z_range must contain [from, to]")
    if normalized["status"] not in {"draft", "ready", "running", "pending", "done", "error"}:
        raise ValueError("Invalid tracking prompt status")
    # The queue is volume-scoped, matching the working label copy. Lock the
    # row so two tabs cannot silently discard one another's parent prompts.
    volume_model = task.volume.__class__
    with transaction.atomic():
        volume = volume_model.objects.select_for_update().get(pk=task.volume_id)
        task.volume = volume
        if tracking_pending_review(task):
            raise ValueError("Confirm or Reject the pending Track preview before editing prompts.")
        prompts = list_tracking_prompts(task)
        prompts = [p for p in prompts if int(p.get("parent_id", 0)) != parent_id]
        prompts.append(normalized)
        _store_tracking_prompts(volume, prompts)
    return normalized


def delete_tracking_prompt(task: AnnotationTask, parent_id: int) -> bool:
    """Delete queue state only; existing parent voxels remain untouched."""
    volume_model = task.volume.__class__
    with transaction.atomic():
        volume = volume_model.objects.select_for_update().get(pk=task.volume_id)
        task.volume = volume
        if tracking_pending_review(task):
            raise ValueError("Confirm or Reject the pending Track preview before editing prompts.")
        prompts = list_tracking_prompts(task)
        kept = [p for p in prompts if int(p.get("parent_id", 0)) != int(parent_id)]
        changed = len(kept) != len(prompts)
        if changed:
            _store_tracking_prompts(volume, kept)
    return changed


def replace_tracking_prompts(task: AnnotationTask, prompts: list[dict]) -> list[dict]:
    """Atomically replace the prompt queue (used by prompt Undo/Redo)."""
    normalized: list[dict] = []
    seen: set[int] = set()
    for prompt in prompts:
        parent_id = int(prompt.get("parent_id", 0))
        if parent_id < 1 or parent_id in seen:
            raise ValueError("Prompt queue contains an invalid or duplicate parent_id")
        seen.add(parent_id)
        subclasses = list(prompt.get("subclasses", []))
        status = str(prompt.get("status", "draft"))
        if status not in {"draft", "ready", "running", "pending", "done", "error"}:
            raise ValueError("Invalid tracking prompt status")
        normalized.append({
            "parent_id": parent_id,
            "subclasses": subclasses,
            "z_range": _tracking_seed_range(subclasses),
            "status": status,
            "note": str(prompt.get("note", "")),
        })
    volume_model = task.volume.__class__
    with transaction.atomic():
        volume = volume_model.objects.select_for_update().get(pk=task.volume_id)
        task.volume = volume
        if tracking_pending_review(task):
            raise ValueError("Confirm or Reject the pending Track preview before editing prompts.")
        _store_tracking_prompts(volume, normalized)
    return normalized


def _mark_tracking_label(volume, final_id: int) -> None:
    from .cellable_port.label_state import LabelOrigin

    store, meta_path = _load_label_metadata_store(volume)
    if str(final_id) in store:
        store.mark_edited(final_id, default_origin=LabelOrigin.TRACKING)
    else:
        store.create_proposed(final_id, LabelOrigin.TRACKING)
    _save_label_metadata_store(store, meta_path)


def _tracking_preview_snapshot_rel(volume) -> str:
    from pathlib import PurePosixPath
    from .label_paths import working_label_rel_path

    working = PurePosixPath(working_label_rel_path(volume))
    return str(working.with_name(f"{working.stem}.track-preview-before.tif"))


def _write_tracking_preview_snapshot(volume, label_mask) -> str:
    import numpy as np
    from .visualization import slice_io
    from .visualization.slice_io import resolve_path

    rel = _tracking_preview_snapshot_rel(volume)
    path = resolve_path(rel)
    slice_io.drop_file(path)
    arr = np.asarray(label_mask, dtype=np.uint16)
    mm = slice_io._create_label_memmap(path, tuple(arr.shape), seed=arr)
    del mm
    slice_io.drop_file(path)
    return rel


def _snapshot_pre_track_labels(volume, label_mask, shape) -> str:
    """Durable pre-Track snapshot of the working labels, cheaply when possible.

    The snapshot is a byte-level record of what Reject must restore. When a
    working-copy file already exists at the right shape, ``label_mask`` *is*
    that file's contents, so copying the file is exactly equivalent to
    re-serialising the array — and costs no RAM and no uint16 re-cast of a
    multi-gigabyte volume. ``_save_label_volume`` wrote it through
    ``_create_label_memmap``, so the copy is memory-mappable on the same terms
    the original was, which is all ``review_tracking_preview`` needs of it.

    Falls back to materialising from the array for the case the fast path
    cannot serve: a volume nobody has painted yet, where ``label_mask`` was
    seeded from the official label (or from zeros) and no working file exists.
    """
    import os
    import shutil

    from .label_paths import working_label_rel_path
    from .visualization import slice_io
    from .visualization.slice_io import resolve_path

    working_path = resolve_path(working_label_rel_path(volume))
    if working_path.exists():
        try:
            import tifffile

            with tifffile.TiffFile(str(working_path)) as handle:
                on_disk = tuple(int(v) for v in handle.series[0].shape)
        except Exception:
            on_disk = None
        if on_disk == tuple(int(v) for v in shape):
            rel = _tracking_preview_snapshot_rel(volume)
            path = resolve_path(rel)
            slice_io.drop_file(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Copy to a temp name and rename, so an interrupted copy can never
            # leave a truncated file that Reject would happily restore from.
            staging = path.with_name(f"{path.name}.partial")
            shutil.copyfile(working_path, staging)
            os.replace(staging, path)
            slice_io.drop_file(path)
            return rel

    return _write_tracking_preview_snapshot(volume, label_mask)


def _delete_tracking_preview_snapshot(rel: str | None) -> None:
    if not rel:
        return
    from core.data_root import assert_owned
    from .visualization.slice_io import drop_file, resolve_path

    path = resolve_path(rel)
    assert_owned(path, what="Track preview snapshot")
    drop_file(path)
    if path.exists():
        path.unlink()


def track_task_batch(
    task: AnnotationTask, groups: list[dict], *, roi_only: bool = False
) -> dict:
    """Atomically propagate many explicit parent/subclass groups.

    The image and working label are opened once and one provider instance is
    reused for every group. SAM2 therefore loads its weights once, while its
    adapter still resets the inference session per independently cropped
    parent. Groups run in request order; existing/unrelated labels and earlier
    groups win collisions. Nothing is persisted until every group succeeds.

    **Memory.** The label volume is the largest thing this function touches, so
    it is held exactly once. It used to be held three times over: the int32
    array from ``_load_or_init_label``, an unconditional ``np.array(…,
    copy=True)`` working duplicate, and a uint16 re-cast of the *original* to
    write the pre-preview snapshot. On this deployment's largest task (volume
    46, 160x3885x4544 uint16, a 5.65 GB working file) that peaked around 34 GB
    inside one gunicorn worker — enough to take the worker, and with three
    workers on one box the service, down whenever two annotators tracked at
    once. The snapshot is now taken from the file on disk (see
    :func:`_snapshot_pre_track_labels`) *before* propagation, which both
    removes the re-cast and means the pre-Track state no longer has to be kept
    in RAM at all: without ``roi_only`` the loaded array is propagated into
    directly, exactly as :func:`track_task_fork` already did. Same peak
    measured at ~17 GB. Snapshot-first also means a failed batch must clean up
    the file it staged, which the ``try`` below does.
    """
    import logging
    import time

    import numpy as np

    from .tracking.registry import get_tracking_provider
    from .tracking.services import run_branch_tracking
    from .visualization.slice_io import _open_volume, resolve_path

    logger = logging.getLogger("mito.track.timing")
    total_started = time.perf_counter()
    if not groups:
        raise ValueError("No tracking groups provided.")
    volume = task.volume
    if tracking_pending_review(task):
        raise ValueError("Confirm or Reject the pending Track preview before propagating again.")
    if not volume.image_location:
        raise ValueError("Volume has no image to track on.")
    # Validate the batch *before* paying for the load. These two checks are
    # pure request validation, and running them afterwards meant a malformed
    # batch still read a multi-gigabyte label volume into RAM first.
    parent_ids = [int(group["parent_id"]) for group in groups]
    if any(parent_id < 1 for parent_id in parent_ids):
        raise ValueError("parent_id must be a positive label id")
    if len(set(parent_ids)) != len(parent_ids):
        raise ValueError("Each parent_id may appear only once per batch")
    _assert_labels_unverified(volume, parent_ids)

    load_started = time.perf_counter()
    image = np.asarray(_open_volume(resolve_path(volume.image_location)))
    labels = _load_or_init_label(volume, image.shape)
    provider = get_tracking_provider()
    logger.info(
        "track_batch task=%s provider=%s groups=%d image_shape=%s load_inputs_ms=%.1f",
        task.pk,
        provider.name,
        len(groups),
        tuple(int(v) for v in image.shape),
        (time.perf_counter() - load_started) * 1000.0,
    )

    # Snapshot first (see the docstring): this is what Reject restores, and
    # taking it from the file on disk is what lets the loaded array be
    # propagated into directly instead of duplicated. Only ``roi_only`` still
    # needs the pre-Track voxels in RAM, to restore everything outside the ROI.
    snapshot_started = time.perf_counter()
    snapshot_rel = _snapshot_pre_track_labels(volume, labels, image.shape)
    logger.info(
        "track_batch task=%s snapshot_ms=%.1f",
        task.pk,
        (time.perf_counter() - snapshot_started) * 1000.0,
    )
    if roi_only:
        original, working = labels, np.array(labels, copy=True)
    else:
        original, working = None, labels

    try:
        results = []
        for position, group in enumerate(groups, start=1):
            parent_started = time.perf_counter()
            z_range = tuple(
                int(v)
                for v in group.get("z_range", (task.z_start, task.z_end - 1))
            )
            if len(z_range) != 2:
                raise ValueError("z_range must contain [from, to]")
            result = run_branch_tracking(
                image=image,
                volume_mask=working,
                seeds={},
                branch_seeds=group.get("branch_seeds", {}),
                z_range=z_range,
                provider=provider,
                group_id=int(group["parent_id"]),
                reserved=parent_ids,
            )
            if not result.get("group"):
                raise ValueError(
                    f"Parent {group['parent_id']} has no non-empty subclass seeds"
                )
            results.append(result)
            logger.info(
                "track_batch task=%s parent=%s position=%d/%d z_range=%s parent_ms=%.1f",
                task.pk,
                group["parent_id"],
                position,
                len(groups),
                z_range,
                (time.perf_counter() - parent_started) * 1000.0,
            )

        if roi_only:
            from .region_mask import protect_volume_outside_roi

            protect_volume_outside_roi(volume, original, working)

        # All provider work succeeded. Publish the merged result for
        # z-scrubbing review. Confirm accepts this working mask; Reject
        # restores the snapshot staged above byte-for-byte by label value.
        publish_started = time.perf_counter()
        _save_label_volume(volume, working)
        metadata = dict(volume.metadata or {})
        completed = set(parent_ids)
        prompts = list_tracking_prompts(task)
        for prompt in prompts:
            if int(prompt.get("parent_id", 0)) in completed:
                prompt["status"] = "pending"
        metadata[TRACKING_PROMPTS_KEY] = {
            "version": TRACKING_PROMPTS_VERSION,
            "items": prompts,
        }
        metadata[TRACKING_PENDING_KEY] = {
            "parent_ids": parent_ids,
            "snapshot_path": snapshot_rel,
            "groups": [result["group"] for result in results],
        }
        volume.metadata = metadata
        volume.save(update_fields=["metadata"])
    except BaseException:
        # Nothing was published, so the staged snapshot has no owner and would
        # otherwise sit in the data root forever (multi-gigabyte, per attempt).
        # Best-effort: a failure to tidy up must never replace the error that
        # actually aborted the batch, which is the one the annotator needs.
        try:
            _delete_tracking_preview_snapshot(snapshot_rel)
        except Exception:
            logger.warning(
                "track_batch task=%s could not remove staged snapshot %s",
                task.pk,
                snapshot_rel,
                exc_info=True,
            )
        raise
    logger.info(
        "track_batch task=%s snapshot_publish_ms=%.1f total_ms=%.1f parents=%s",
        task.pk,
        (time.perf_counter() - publish_started) * 1000.0,
        (time.perf_counter() - total_started) * 1000.0,
        parent_ids,
    )
    return {
        "results": results,
        "done": len(results),
        "total": len(groups),
        "pending_review": {"parent_ids": parent_ids},
    }


def plan_track_task_batch(
    task: AnnotationTask,
    groups: list[dict],
    *,
    axis: str = "z",
    pending_slices=None,
    overwrite_mode: str | None = None,
) -> dict:
    """Run SAM propagation as a read-only plan for the browser's undo buffer.

    Tracking seeds and ranges are z-based, so pending label planes and returned
    diffs are deliberately axial. This avoids a temporary preview file and,
    crucially, never calls ``_save_label_volume``.
    """
    import os
    import numpy as np

    from .tracking.registry import get_tracking_provider
    from .tracking.services import run_branch_tracking
    from .tools.overwrite import DEFAULT_OVERWRITE_MODE, is_valid_mode
    from .visualization.slice_io import _open_volume, resolve_path

    if axis != "z":
        raise ValueError("Track propagation is axial; switch the viewer to z first.")
    if not groups:
        raise ValueError("No tracking groups provided.")
    overwrite_mode = overwrite_mode or DEFAULT_OVERWRITE_MODE
    if not is_valid_mode(overwrite_mode):
        raise ValueError(f"Unknown overwrite mode {overwrite_mode!r}.")
    volume = task.volume
    if not volume.image_location:
        raise ValueError("Volume has no image to track on.")
    parent_ids = [int(group["parent_id"]) for group in groups]
    if any(parent_id < 1 for parent_id in parent_ids):
        raise ValueError("parent_id must be a positive label id")
    if len(set(parent_ids)) != len(parent_ids):
        raise ValueError("Each parent_id may appear only once per batch")
    _assert_labels_unverified(volume, parent_ids)

    ranges = []
    for group in groups:
        z_range = tuple(int(value) for value in group.get("z_range", (0, 0)))
        if len(z_range) != 2:
            raise ValueError("z_range must contain [from, to]")
        ranges.append(tuple(sorted(z_range)))
    slab_lo = min(lo for lo, _hi in ranges)
    slab_hi = max(hi for _lo, hi in ranges)

    image_source = _open_volume(resolve_path(volume.image_location))
    if slab_lo < 0 or slab_hi >= int(image_source.shape[0]):
        raise ValueError(
            f"Track range {(slab_lo, slab_hi)} is outside 0..{int(image_source.shape[0]) - 1}"
        )
    slab_voxels = (
        (slab_hi - slab_lo + 1)
        * int(image_source.shape[1])
        * int(image_source.shape[2])
    )
    slab_limit = int(
        os.environ.get("MITO_TRACK_PLAN_MAX_VOXELS", "256000000")
    )
    if slab_voxels > slab_limit:
        raise ValueError(
            f"Track range has {slab_voxels:,} voxels; bounded plan limit is "
            f"{slab_limit:,}. Move the seed layers closer or split the run."
        )
    # Detach the bounded slab before slow model compute. The shared source
    # handle remains owned by slice_io's bounded LRU, not by this request.
    image = np.array(image_source[slab_lo : slab_hi + 1], copy=True)
    image_source = None

    reader = _LazyPlanLabels(task, "z", pending_slices)
    try:
        working = np.stack(
            [reader.read_axis("z", z) for z in range(slab_lo, slab_hi + 1)]
        )
        global_max = max(
            get_label_max_id_readonly(volume),
            max(parent_ids),
            max(
                (int(plane.max()) for plane in reader.pending.values() if plane.size),
                default=0,
            ),
        )
        provider = get_tracking_provider()
        results = []
        for group, (lo, hi) in zip(groups, ranges):
            local_branch_seeds = {
                int(child): {int(z) - slab_lo: mask for z, mask in per_z.items()}
                for child, per_z in group.get("branch_seeds", {}).items()
            }
            result = run_branch_tracking(
                image=image,
                volume_mask=working,
                seeds={},
                branch_seeds=local_branch_seeds,
                z_range=(lo - slab_lo, hi - slab_lo),
                provider=provider,
                group_id=int(group["parent_id"]),
                reserved=parent_ids,
                branch_id_floor=global_max + 1,
                protect_other_labels=overwrite_mode == DEFAULT_OVERWRITE_MODE,
            )
            if not result.get("group"):
                raise ValueError(
                    f"Parent {group['parent_id']} has no non-empty subclass seeds"
                )
            audit = result["group"]
            if audit.get("seed_z") is not None:
                audit["seed_z"] = int(audit["seed_z"]) + slab_lo
            audit["seed_zs"] = [int(z) + slab_lo for z in audit.get("seed_zs", [])]
            results.append(result)
        bbox = (slab_lo, slab_hi + 1, 0, reader.shape[1], 0, reader.shape[2])
        slices = _planned_crop_slices(reader, "z", bbox, working)
    finally:
        reader.close()
    return {
        "results": results,
        "done": len(results),
        "total": len(groups),
        "axis": "z",
        "overwrite_mode": overwrite_mode,
        "slices": slices,
    }


@_serialized_task_volume_write
def review_tracking_preview(task: AnnotationTask, action: str) -> dict:
    """Confirm a pending Track result or reject it and restore its snapshot."""
    import numpy as np
    from .visualization.slice_io import read_label_array, resolve_path

    action = str(action).lower()
    if action not in {"confirm", "reject"}:
        raise ValueError("action must be 'confirm' or 'reject'")
    volume_model = task.volume.__class__
    with transaction.atomic():
        volume = volume_model.objects.select_for_update().get(pk=task.volume_id)
        task.volume = volume
        metadata = dict(volume.metadata or {})
        pending = metadata.get(TRACKING_PENDING_KEY)
        if not isinstance(pending, dict):
            raise ValueError("There is no pending Track preview to review.")
        parent_ids = {int(value) for value in pending.get("parent_ids", [])}
        snapshot_rel = str(pending.get("snapshot_path", ""))
        prompts = list_tracking_prompts(task)

        if action == "reject":
            snapshot_path = resolve_path(snapshot_rel)
            if not snapshot_path.exists():
                raise ValueError("Track preview snapshot is missing; cannot safely Reject.")
            before = np.asarray(read_label_array(snapshot_path), dtype=np.int32)
            _save_label_volume(volume, before)
            for prompt in prompts:
                if int(prompt.get("parent_id", 0)) in parent_ids:
                    has_seeds = any(child.get("seeds") for child in prompt.get("subclasses", []))
                    prompt["status"] = "ready" if has_seeds else "draft"
        else:
            prompts = [
                prompt for prompt in prompts
                if int(prompt.get("parent_id", 0)) not in parent_ids
            ]
            tracking_groups = list(metadata.get("tracking_groups", []))
            tracking_groups.extend(pending.get("groups", []))
            metadata["tracking_groups"] = tracking_groups

        metadata[TRACKING_PROMPTS_KEY] = {
            "version": TRACKING_PROMPTS_VERSION,
            "items": prompts,
        }
        metadata.pop(TRACKING_PENDING_KEY, None)
        volume.metadata = metadata
        volume.save(update_fields=["metadata"])

    if action == "confirm":
        for parent_id in parent_ids:
            _mark_tracking_label(volume, parent_id)
    _delete_tracking_preview_snapshot(snapshot_rel)
    return {
        "action": action,
        "parent_ids": sorted(parent_ids),
        "items": prompts,
    }


@_serialized_task_volume_write
def track_task_fork(
    task: AnnotationTask,
    seeds_by_z,
    *,
    z_range=None,
    group_id: int | None = None,
    roi_only: bool = False,
) -> dict:
    """Run fork-aware SAM2 tracking for one mito on ``task`` and persist it.

    ``seeds_by_z`` maps ``z -> 2D bool mask``. Forks are split into temporary
    branch tracks, propagated, then auto-merged into one instance (see
    :func:`annotation.tracking.services.run_branch_tracking`). The merged
    label volume is written to the volume's *working* copy only (see the
    module note above) — it does not become the volume's official label
    until a submission referencing it is approved. Group membership is
    recorded in ``volume.metadata['tracking_groups']`` for audit / undo /
    re-run regardless (that's bookkeeping, not the label pixels themselves).

    **Starts from the working copy when one exists**, falling back to the
    official label only for a volume nobody has painted yet — see
    :func:`_load_or_init_label`, which enforces that precedence for every
    whole-volume operation. This matters here in particular because the result
    is written back over the whole volume.
    """
    import numpy as np

    from .tracking.services import run_branch_tracking
    from .visualization.slice_io import _open_volume, resolve_path

    volume = task.volume
    if not volume.image_location:
        raise ValueError("Volume has no image to track on.")

    image = np.asarray(_open_volume(resolve_path(volume.image_location)))
    label_mask = _load_or_init_label(volume, image.shape)
    original = np.array(label_mask, copy=True) if roi_only else None

    if z_range is None:
        z_range = (task.z_start, max(task.z_start, task.z_end - 1))

    result = run_branch_tracking(
        image=image,
        volume_mask=label_mask,
        seeds=seeds_by_z,
        z_range=z_range,
        group_id=group_id,
    )

    if roi_only:
        from .region_mask import protect_volume_outside_roi

        protect_volume_outside_roi(volume, original, label_mask)

    _save_label_volume(volume, label_mask)
    groups = list((volume.metadata or {}).get("tracking_groups", []))
    if result.get("group"):
        groups.append(result["group"])
    volume.metadata = {**(volume.metadata or {}), "tracking_groups": groups}
    volume.save(update_fields=["metadata"])

    # Lifecycle: the propagated instance is new-to-review PROPOSED/TRACKING
    # if this is the first time it's tracked, else just marked EDITED (its
    # shape changed again) — mirrors the AI/WATERSHED "automated result
    # needs a human look" convention (see set_label_slice_ids's docstring).
    final_id = result.get("final_id")
    if final_id:
        _mark_tracking_label(volume, final_id)

    return result


# --- Label-id read/write (in-app brush/eraser editor) -----------------------
#
# This is the hot path — called on every slice navigation and every painted
# stroke — so unlike track_task_fork above (rare, needs a full in-memory
# array for its algorithm) it must never read or write more than the one
# touched slice. Measured cost of getting this wrong on a real-sized label
# volume: ~8.75s per stroke (full imread + full imwrite). Fixed cost with a
# writable memmap, touching one plane: ~0.015s.

def _adopt_legacy_working_copy(volume) -> None:
    """One-time, in-place migration of a volume's pre-``_mask``-scheme working
    files to the current layout, run lazily the first time the editor touches
    a volume. Prevents already-painted voxels from silently disappearing from
    the Labels panel when the naming scheme changes without the operator
    having run ``migrate_volume_artifacts`` first.

    Renames ``<dataset>/volume_<id>_labels.tif`` →
    ``<dataset>/<stem>_mask.tif`` and its sidecar into ``metadata/`` — only
    when the new mask doesn't already exist (never overwrites current work)
    and the legacy file does. A no-op (and cheap: two ``exists()`` checks)
    once migrated. Failures are swallowed to a fresh start rather than
    crashing the editor — the seed-from-official-label path below still runs.
    """
    from .label_paths import (
        legacy_working_label_metadata_rel_path,
        legacy_working_label_rel_path,
        working_label_metadata_rel_path,
        working_label_rel_path,
    )
    from .visualization.slice_io import resolve_path

    new_path = resolve_path(working_label_rel_path(volume))
    legacy_path = resolve_path(legacy_working_label_rel_path(volume))
    try:
        # Mask and metadata migrations are intentionally independent.  An
        # earlier release could create the new mask before moving its legacy
        # sidecar; returning as soon as the mask existed made every verified
        # label look Proposed after the next reopen even though the durable
        # metadata was still present under the old name.
        if not new_path.exists() and legacy_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.replace(new_path)
        legacy_meta = resolve_path(legacy_working_label_metadata_rel_path(volume))
        new_meta = resolve_path(working_label_metadata_rel_path(volume))
        if legacy_meta.exists() and not new_meta.exists():
            new_meta.parent.mkdir(parents=True, exist_ok=True)
            legacy_meta.replace(new_meta)
    except OSError:
        # Best-effort: if the rename fails (permissions/NFS), fall through to
        # a normal fresh working copy rather than taking the editor down.
        return


def _seed_working_label(volume, owned_path, shape, source_location: str) -> None:
    """Create the working label file at ``owned_path``, seeded from
    ``source_location`` (the registered/official label) or from zeros.

    Split out of :func:`_writable_label` so "reset this task's working labels
    back to what was registered" is *the same code path* as "create the working
    copy for the first time", rather than a second, subtly different copier —
    see :func:`reset_working_labels_to_registered`. The source is only ever
    read: an externally registered label belongs to whoever produced it.
    """
    import numpy as np

    from .visualization.hdf5_io import (
        copy_into_label_memmap,
        is_hdf5_path,
        open_hdf5_volume,
    )
    from .visualization.nifti_io import is_nifti_path, open_nifti_volume
    from .visualization.slice_io import resolve_path

    seed = None
    stream = None
    if source_location:
        src = resolve_path(source_location)
        if src.exists() and src != owned_path and (
            is_hdf5_path(src) or is_nifti_path(src)
        ):
            # HDF5/NIfTI masks are streamed plane-block by plane-block into the
            # working copy instead of being materialised first: the p10
            # masks are ~3 MB compressed but 2.1 GB uncompressed, and
            # ``imread``-then-write would pay that twice, in every worker
            # that opens the volume for the first time.
            try:
                stream = (
                    open_hdf5_volume(src)
                    if is_hdf5_path(src)
                    else open_nifti_volume(src)
                )
            except Exception:
                # Same policy as the TIFF branch below — an unreadable
                # registered label means "start empty", never an error out
                # of the editor's entry point.
                stream = None
            if stream is not None and tuple(stream.shape) != tuple(shape):
                stream = None
        elif src.exists() and src != owned_path:
            import tifffile

            try:
                arr = np.asarray(tifffile.imread(str(src)))
            except Exception:
                # The official label may be registered *by reference* to a
                # file this app does not own — an externally produced
                # prediction, a truncated transfer, a path that exists but
                # is not a readable TIFF. Never quarantine or rewrite it
                # (it is someone else's data); an unreadable seed just
                # means "start this working copy empty", the same policy
                # ``_load_or_init_label`` already applies.
                #
                # Without this, the first read *or* save on such a volume
                # raises out of the editor's entry point and the annotator
                # cannot work on it at all — not even from scratch.
                arr = None
            if arr is not None and arr.shape == tuple(shape):
                seed = arr.astype(np.uint16)

    from .visualization import slice_io

    # Memmap-compatible create (not bare imwrite) so paint/summary can
    # open the working copy with tifffile.memmap afterwards.
    mm = slice_io._create_label_memmap(owned_path, tuple(shape), seed=seed)
    max_id = int(seed.max()) if seed is not None and seed.size else 0
    if stream is not None:
        max_id = copy_into_label_memmap(mm, stream)
        close = getattr(stream, "close", None)
        if close is not None:
            close()
    del mm
    slice_io.set_label_max_id(owned_path, max_id)


def _writable_label(volume, shape):
    """A writable memmap over the volume's *working* label file, seeding it
    from the current official label (or zeros) the first time it's
    touched. Returns ``(memmap, owned_rel_path)``."""
    from .label_paths import working_label_rel_path
    from .visualization.slice_io import open_label_volume_writable, resolve_path

    _adopt_legacy_working_copy(volume)
    owned_rel = working_label_rel_path(volume)
    owned_path = resolve_path(owned_rel)

    if not owned_path.exists():
        _seed_working_label(volume, owned_path, shape, volume.label_location)

    return open_label_volume_writable(owned_path, shape), owned_rel


def _load_label_metadata_store(volume):
    """Load (or start empty) the per-label lifecycle-state sidecar for
    ``volume``'s working copy. Returns ``(store, path_str)``."""
    from .cellable_port.label_state import LabelMetadataStore
    from .label_paths import working_label_metadata_rel_path
    from .visualization.slice_io import resolve_path

    # The working mask can already use the current name while its sidecar is
    # still at the legacy path.  Adopt both independently before loading so a
    # reopen never silently drops lifecycle state.
    _adopt_legacy_working_copy(volume)
    path = resolve_path(working_label_metadata_rel_path(volume))
    store = LabelMetadataStore()
    loaded = store.load(str(path))
    backup = path.with_name(f"{path.name}.bak")
    if not loaded and (path.exists() or backup.exists()):
        raise ValueError(
            "Label lifecycle metadata is unreadable in both its primary and "
            "backup files. No lifecycle changes were written; restore the "
            "sidecar before continuing."
        )
    return store, str(path)


def _save_label_metadata_store(store, path_str: str) -> None:
    """Persist the lifecycle sidecar beside its working mask.

    Guarded like the mask itself: the sidecar is derived from the same
    volume-owned path, so if one could ever escape the data root the other
    could too. See ``core/data_root.py``.
    """
    import os

    from core.data_root import assert_owned

    assert_owned(path_str, what="label metadata sidecar")
    os.makedirs(os.path.dirname(path_str), exist_ok=True)
    store.save(path_str)


class VerifiedLabelConflict(ValueError):
    """A raster mutation attempted to change a label still marked Verified."""


def _verified_label_ids(volume) -> set[int]:
    store, _path = _load_label_metadata_store(volume)
    return store.verified_ids()


def _assert_verified_labels_unchanged(
    volume, before, after, *, protected: set[int] | None = None
) -> None:
    """Refuse any geometry change involving a Verified instance.

    Verification is an edit lock, not merely a display badge. Both removing
    existing verified voxels and growing a verified id into new voxels require
    an explicit Unverify first.
    """
    import numpy as np

    protected = _verified_label_ids(volume) if protected is None else protected
    if not protected:
        return
    old = np.asarray(before)
    new = np.asarray(after)
    if old.shape != new.shape:
        raise ValueError(f"Label shape changed from {old.shape} to {new.shape}.")
    changed = old != new
    if not changed.any():
        return
    touched = {
        int(value)
        for value in np.concatenate((old[changed], new[changed]))
        if int(value) > 0
    }
    blocked = sorted(touched & protected)
    if blocked:
        labels = ", ".join(str(value) for value in blocked[:8])
        suffix = "…" if len(blocked) > 8 else ""
        raise VerifiedLabelConflict(
            f"Verified label(s) {labels}{suffix} are locked. Unverify them "
            "before changing their voxels."
        )


def _assert_verified_volume_unchanged(volume, before, after) -> None:
    """Bounded-memory whole-volume form of the Verified edit lock."""
    if tuple(before.shape) != tuple(after.shape):
        raise ValueError(
            f"Label shape changed from {tuple(before.shape)} to {tuple(after.shape)}."
        )
    protected = _verified_label_ids(volume)
    if not protected:
        return
    for start in range(0, int(before.shape[0]), 8):
        _assert_verified_labels_unchanged(
            volume,
            before[start : start + 8],
            after[start : start + 8],
            protected=protected,
        )


def _assert_labels_unverified(volume, label_ids) -> None:
    blocked = sorted(_verified_label_ids(volume) & {int(value) for value in label_ids})
    if blocked:
        labels = ", ".join(str(value) for value in blocked)
        raise VerifiedLabelConflict(
            f"Verified label(s) {labels} are locked. Unverify them before "
            "running this tool."
        )


def get_label_slice_ids(volume, axis: str, index: int) -> dict:
    """Raw instance ids for one label slice, RLE-encoded for the editor.

    Two-phase locking on purpose. Seeding/adopting the working copy rewrites
    bytes and has to be exclusive, but that is first-touch only; the editor's
    steady state is pure reads. Holding the exclusive lane for every read
    serialized all label-slice traffic for a volume across every Gunicorn
    worker, so scrub prefetch queued behind itself and two annotators on one
    volume blocked each other.
    """
    import numpy as np

    from .label_paths import working_label_rel_path
    from .visualization.slice_io import (
        AXES,
        SliceIOError,
        _open_volume,
        encode_label_rle,
        resolve_path,
        serialized_file_write,
    )

    if not volume.image_location:
        raise ValueError("Volume has no image.")
    image = _open_volume(resolve_path(volume.image_location))
    owned_path = resolve_path(working_label_rel_path(volume))

    def _encode(mm) -> dict:
        axis_i = AXES[axis]
        n = mm.shape[axis_i]
        idx = max(0, min(int(index), n - 1))
        # Deliberately a view, not `_read_axis_slice`'s copy: this plane can be
        # 16 MB on a 2048² volume and `encode_label_rle` only reads it.
        if axis == "z":
            sl = np.asarray(mm[idx])
        elif axis == "y":
            sl = np.asarray(mm[:, idx, :])
        else:
            sl = np.asarray(mm[:, :, idx])
        return {
            "shape": list(sl.shape),
            "runs": encode_label_rle(sl),
            "revision": _working_label_revision(owned_path),
        }

    if owned_path.exists():
        try:
            with serialized_file_write(owned_path, shared=True):
                return _encode(_open_volume(owned_path))
        except (SliceIOError, OSError, ValueError):
            # Corrupt, wrong-shaped or non-memmapable working copy. Fall
            # through to the exclusive lane, where `_writable_label` rebuilds
            # it (salvaging voxels when it can) exactly as it always has.
            pass

    with serialized_file_write(owned_path):
        mm, _ = _writable_label(volume, image.shape)
        return _encode(mm)


class LabelWriteConflict(ValueError):
    """The working copy changed after this client loaded its baseline."""


def _working_label_revision(path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}:{stat.st_ino}"


def _visible_label_path(volume):
    """Existing working label, then official label, without filesystem writes."""
    from .label_paths import working_label_rel_path
    from .visualization.slice_io import resolve_path

    working = resolve_path(working_label_rel_path(volume))
    if working.exists():
        return working
    if volume.label_location:
        official = resolve_path(volume.label_location)
        if official.exists():
            return official
    return None


def get_label_slice_ids_readonly(volume, axis: str, index: int) -> dict:
    import numpy as np

    from .visualization.slice_io import AXES, _open_volume, encode_label_rle, resolve_path

    path = _visible_label_path(volume)
    if path is None:
        if not volume.image_location:
            raise ValueError("Volume has no image.")
        image = _open_volume(resolve_path(volume.image_location))
        shape = list(image.shape)
        axis_index = AXES[axis]
        if not 0 <= int(index) < shape[axis_index]:
            raise ValueError(f"index {index} is out of range for axis {axis!r}.")
        plane_shape = tuple(shape[i] for i in range(3) if i != axis_index)
        slice_array = np.zeros(plane_shape, dtype=np.int32)
    else:
        array = _open_volume(path)
        axis_index = AXES[axis]
        idx = int(index)
        if not 0 <= idx < array.shape[axis_index]:
            raise ValueError(f"index {idx} is out of range for axis {axis!r}.")
        slice_array = (
            np.asarray(array[idx])
            if axis == "z"
            else np.asarray(array[:, idx, :])
            if axis == "y"
            else np.asarray(array[:, :, idx])
        )
    return {"shape": list(slice_array.shape), "runs": encode_label_rle(slice_array)}


@_serialized_volume_write
def set_label_slice_ids(
    volume, axis: str, index: int, shape, runs, *, origin: str = "manual",
    roi_only: bool = False, expected_revision: str = "",
) -> int:
    """Write one label slice's raw instance ids (from the editor) and persist.

    Touches only the written slice's pages on disk (see the module note
    above) — returns the max instance id now present in the whole volume, so
    the client can offer the next "new instance" id without a second round
    trip (a cheap ``mm.max()`` over an already-open memmap, not a fresh read).

    This only ever touches the *working* copy — never ``volume.label_path``/
    ``label_file``/``label_type`` (the official, approved label). Those only
    change in ``approve_submission``, once a manager approves a submission
    referencing this working copy.

    **Label lifecycle tracking** (``cellable_port/label_state.py``): diffs
    the slice's previous content against ``runs`` and updates the per-label
    state sidecar for every instance id whose pixels actually changed on
    this slice (added, removed, or repainted) — never for ids merely present
    but untouched, since a commit always resends the *whole* slice, not just
    a delta. ``origin`` (``"manual"`` — brush/erase/box-erase — or ``"ai"``
    — a committed Point/Box/Boundary preview) only matters for an id that
    doesn't exist in the store *yet*: a brand-new manual id starts EDITED
    (a human just drew it), a brand-new AI id starts PROPOSED with a
    single-slice snapshot recorded (so it can be reverted) — matching
    Cellable's ``get_or_create``/``_registerAutoSegmentationLabels``. An id
    that already has tracked state is marked EDITED on further changes,
    regardless of ``origin``. VERIFIED is the exception: its geometry is
    locked and this write is rejected until the user explicitly Unverifies.
    """
    import numpy as np

    from .cellable_port.label_state import LabelOrigin
    from .visualization.slice_io import AXES, _open_volume, decode_label_rle, encode_label_rle, resolve_path

    if not volume.image_location:
        raise ValueError("Volume has no image.")
    image = _open_volume(resolve_path(volume.image_location))
    mm, owned_rel = _writable_label(volume, image.shape)
    axis_i = AXES[axis]
    n = mm.shape[axis_i]
    # **Reject** an out-of-range index rather than clamping it. Reads clamp
    # (returning the nearest slice is harmless), but this is a destructive
    # whole-slice replacement: clamping would silently paint the caller's
    # pixels onto the *last* slice of the axis and destroy whatever was
    # labelled there. That turns a client bug — a stale index, or an index
    # belonging to a different axis after a switch — into irreversible data
    # loss on a slice the user never opened. The API renders ValueError as 400.
    idx = int(index)
    if idx < 0 or idx >= n:
        raise ValueError(
            f"index {idx} is out of range for axis {axis!r} (0..{n - 1})."
        )
    sl = decode_label_rle(runs, tuple(shape)).astype(mm.dtype)

    owned_path = resolve_path(owned_rel)
    if expected_revision and expected_revision != _working_label_revision(owned_path):
        raise LabelWriteConflict(
            "This working volume changed in another tab or session. Reload the "
            "layer before saving so newer annotation work is not overwritten."
        )
    try:
        mtime_ns_before = owned_path.stat().st_mtime_ns
    except OSError:
        mtime_ns_before = None

    old_sl = _read_axis_slice(mm, axis, idx)
    if roi_only:
        from .region_mask import protect_slice_outside_roi

        sl = protect_slice_outside_roi(volume, axis, idx, old_sl, sl)
    _assert_verified_labels_unchanged(volume, old_sl, sl)
    _write_axis_slice(mm, axis, idx, sl)
    mm.flush()

    # Keep the Labels "All" summary current *without* rescanning the volume.
    # The summary is a sum over per-slice statistics, so a z write only has to
    # replace this slice's contribution — the alternative (invalidate, rescan
    # on the next read) cost 10-27s on the volumes here, on the very request
    # that follows every Save. A y/x write spans every z, so there's no single
    # slice to fold in: drop the cache and let the next read rebuild it.
    from .cellable_port import labels_3d as _labels_3d

    if axis == "z" and mtime_ns_before is not None:
        _labels_3d.update_summary_for_slice(
            owned_path, idx, sl, mtime_ns_before=mtime_ns_before
        )
    else:
        _labels_3d.forget_summary(owned_path)

    from .visualization import slice_io

    max_id = slice_io.bump_label_max_id(resolve_path(owned_rel), mm, int(sl.max()))
    # Only this volume's working label went stale. Dropping every cached slice
    # of every volume (what a bare call does) meant one annotator's routine
    # slice save evicted everyone else's warm EM slices — and this volume's
    # own *image* slices, which a label write cannot possibly stale.
    slice_io.invalidate_read_caches(resolve_path(owned_rel))

    changed = old_sl != sl
    if changed.any():
        touched_ids = (set(np.unique(old_sl[changed]).tolist()) | set(np.unique(sl[changed]).tolist())) - {0}
        if touched_ids:
            store, meta_path = _load_label_metadata_store(volume)
            ai_origin = LabelOrigin.AI if origin == "ai" else LabelOrigin.MANUAL
            for label_id in touched_ids:
                if str(label_id) in store:
                    store.mark_edited(label_id, default_origin=ai_origin)
                elif origin == "ai":
                    footprint = (sl == label_id).astype(np.int32)
                    store.create_proposed(
                        label_id,
                        LabelOrigin.AI,
                        snapshot_z=int(index) if axis == "z" else None,
                        snapshot_shape=tuple(shape),
                        snapshot_rle=encode_label_rle(footprint),
                    )
                else:
                    store.get_or_create(label_id, origin=LabelOrigin.MANUAL)
            _save_label_metadata_store(store, meta_path)

    return max_id


def get_label_max_id(volume) -> int:
    """Highest instance id currently in the volume's *working* label copy, or 0.

    Bootstraps the editor's "next new instance id" — must read the working
    copy (what the editor actually paints into), not ``label_location`` (the
    official, approved label, which can lag behind or be entirely empty
    while a task is still being annotated).

    Cached per file after the first call (see ``slice_io.label_max_id``) —
    an O(volume size) scan is fine once, not on every editor page load.
    """
    from .label_paths import working_label_rel_path
    from .visualization.slice_io import _open_volume, label_max_id, resolve_path

    path = resolve_path(working_label_rel_path(volume))
    if not path.exists():
        return 0
    arr = _open_volume(path)
    return label_max_id(path, arr)


def get_label_max_id_readonly(volume) -> int:
    from .visualization.slice_io import _open_volume, label_max_id

    path = _visible_label_path(volume)
    if path is None:
        return 0
    return label_max_id(path, _open_volume(path))


# --- Cellable-ported interactive AI tools (Point/Box/Boundary mask) --------
#
# Read-only "preview a candidate mask" operations — unlike tracking/watershed
# below, these never write to the working label copy themselves. The client
# merges the returned mask into its already-loaded slice locally (same as a
# brush stroke) and commits through the existing ``set_label_slice_ids`` path
# above, so no new persistence code is needed here. See
# ``cellable_port/ai/efficient_sam.py`` (ported from Cellable's
# ``labelme/ai/efficient_sam.py``) for the model itself.

def _ai_embedding_cache_path(volume, axis, index):
    """Resolve the on-disk embedding-cache path for one (volume, axis,
    index) under the currently-configured EfficientSAM variant — see
    ``cellable_port/ai/embed_cache.py`` for the key/invalidation design.
    Returns ``None`` if there's no image to key off of."""
    from .cellable_port.ai.application import embedding_cache_path

    return embedding_cache_path(volume, axis, index)


# Small bounded cache of AI-normalized slices. `normalize_for_ai` is a
# full-slice percentile stretch (O(pixels) — ~300ms on a ~7MP EM slice) and
# was being recomputed on *every* warm and *every* predict click of the same
# slice, even though the raw slice is already cached in `slice_io`. Both the
# warm and predict paths key their in-process embedding cache off the
# normalized image's bytes, so caching the normalized array here means a
# warmed slice's subsequent clicks skip that full-slice work entirely. Keyed
# by (image path, axis, index, mtime) so a replaced image invalidates it.
from collections import OrderedDict as _OrderedDict  # noqa: E402

_MAX_NORMALIZED_SLICE_CACHE = 16
_normalized_slice_cache: "_OrderedDict[tuple, object]" = _OrderedDict()


def _normalized_ai_slice(volume, axis, index):
    """AI-normalized (uint8) slice for (volume, axis, index), bounded-LRU
    cached so warm + repeated predicts on one slice don't re-run the
    full-slice percentile stretch each time."""
    from .cellable_port.ai.normalize import normalize_for_ai
    from .visualization.slice_io import read_slice, resolve_path

    p = resolve_path(volume.image_location)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    key = (str(p), axis, int(index), mtime)
    cached = _normalized_slice_cache.get(key)
    if cached is not None:
        _normalized_slice_cache.move_to_end(key)
        return cached
    image = normalize_for_ai(read_slice(volume.image_location, axis, index))
    _normalized_slice_cache[key] = image
    _normalized_slice_cache.move_to_end(key)
    while len(_normalized_slice_cache) > _MAX_NORMALIZED_SLICE_CACHE:
        _normalized_slice_cache.popitem(last=False)
    return image


def predict_ai_mask(
    task, axis, index, mode, *, points=None, point_labels=None, box=None,
    roi_only: bool = False,
) -> dict:
    """Run the ported EfficientSAM model on one image slice.

    ``mode`` is ``"points"`` (Point Mask tool), ``"box"`` (Box Mask tool), or
    ``"boundary"`` (Boundary tool — the same points-mask prediction, then
    turned into a ring via erode/dilate, ported from Cellable's
    ``Canvas._finaliseImpl`` ai_boundary branch). Returns a boolean mask as
    ``{"shape": [h, w], "runs": [[0/1, count], ...]}`` — reusing
    :func:`annotation.visualization.slice_io.encode_label_rle` since a
    boolean mask is just a label slice with two possible values.

    The image fed to the model is normalized with
    :func:`cellable_port.ai.normalize.normalize_for_ai` (Cellable's own
    ``normalizeImg``), **not** ``slice_io.display_range`` — see that
    module's docstring for why a display-stable, whole-volume stretch and
    the per-slice non-zero-percentile stretch Cellable actually feeds its
    model are two different things, and conflating them was a real source
    of point/box mask divergence from local Cellable
    (`progress/history/21-cellable-parity-followups.md`). Brightness/
    contrast are **never** part of this — those are client-side CSS filters
    on the display image only (`progress/history/23-cellable-parity-ort-
    and-prompt-ux.md`): baking them into the AI input would make prediction
    quality depend on wherever the user last left those sliders, which is
    strictly worse than Cellable's own behavior, not "more faithful" to it.

    The embedding this computes is shared with :func:`warm_ai_embedding`
    via the same on-disk cache (``_ai_embedding_cache_path`` /
    ``cellable_port/ai/embed_cache.py``) — a slice warmed ahead of time (on
    slice-open or AI-tool entry) makes this call decoder-only.
    """
    from .cellable_port.ai.application import predict_mask

    result = predict_mask(
        task,
        axis,
        index,
        mode,
        points=points,
        point_labels=point_labels,
        box=box,
    )
    if roi_only:
        from .region_mask import region_mask_slice
        from .visualization.slice_io import decode_label_rle, encode_label_rle

        shape = tuple(result["shape"])
        predicted = decode_label_rle(result["runs"], shape) != 0
        predicted &= region_mask_slice(task.volume, axis, index, shape)
        result = {**result, "runs": encode_label_rle(predicted.astype("uint8"))}
    return result


def warm_ai_embedding(task, axis, index, *, point=None) -> bool:
    """Pre-compute (and cache, in-process + on-disk) the EfficientSAM
    embedding for one slice, without predicting anything — called when the
    Annotate slice changes or an AI tool is entered, so the *first* actual
    click only has to run the (fast) decoder. Mirrors the intent of
    Cellable's background embedding thread (`app.py`'s
    ``_compute_and_cache_image_embedding``), adapted to a stateless request
    instead of a long-lived Qt session — see ``EfficientSam.warm``.

    Returns ``True`` if it ran, ``False`` if there's simply no image at this
    slice (not an error). Raises :class:`AiUnavailable` the same way
    :func:`predict_ai_mask` does if the model isn't installed/configured —
    callers should treat that as "nothing to warm," not a hard failure.
    """
    from .cellable_port.ai.application import warm_embedding

    return warm_embedding(task, axis, index, point=point)


# --- Cellable-ported 3D watershed (Seeds tool) ------------------------------

@_serialized_task_volume_write
def run_watershed_task(
    task, target_label: int, seeds_zyx, *, padding: int = 5,
    roi_only: bool = False,
) -> dict:
    """Split ``target_label`` via 3D watershed seeded at ``seeds_zyx``
    (``[(z, y, x), ...]``), persisting the result to the volume's *working*
    label copy — same whole-volume read/mutate/write shape as
    :func:`track_task_fork` above (rare, user-triggered, needs real 3D array
    semantics), and subject to the same staging rule: this never touches
    ``volume.label_path``/``label_file``. See
    ``cellable_port/watershed.py`` (ported from Cellable's
    ``apply_3d_watershed``) for the segmentation itself.

    **Reads the working copy, not the official label.** Seeds/watershed exists
    to refine an instance the annotator is actively painting, so it must see
    already-painted-but-not-yet-approved pixels.
    """
    import numpy as np

    from .cellable_port.watershed import WatershedError, run_watershed_3d
    from .label_paths import working_label_rel_path
    from .visualization.slice_io import resolve_path

    volume = task.volume
    working_path = resolve_path(working_label_rel_path(volume))
    if not working_path.exists():
        raise ValueError("Nothing has been painted for this volume yet.")
    from .visualization.slice_io import read_label_array

    label_mask = read_label_array(working_path)
    original = np.array(label_mask, copy=True) if roi_only else None
    try:
        result = run_watershed_3d(label_mask, target_label, seeds_zyx, padding=padding)
    except WatershedError as exc:
        raise ValueError(str(exc)) from exc
    if roi_only:
        from .region_mask import protect_volume_outside_roi

        protect_volume_outside_roi(volume, original, label_mask)
    _save_label_volume(volume, label_mask)

    # Lifecycle: the target label's shape just changed (mark EDITED); every
    # newly-split-off id is registered PROPOSED/WATERSHED with **no**
    # snapshot — matches Cellable's own
    # ``_registerAutoSegmentationLabels(..., store_snapshots=False)`` call
    # for watershed output (a multi-region split isn't a single easily
    # revertible "before" state the way one AI-mask commit is).
    from .cellable_port.label_state import LabelOrigin

    store, meta_path = _load_label_metadata_store(volume)
    store.mark_edited(result["target_label"], default_origin=LabelOrigin.WATERSHED)
    for new_id in result["new_label_ids"]:
        store.create_proposed(new_id, LabelOrigin.WATERSHED)
    _save_label_metadata_store(store, meta_path)

    return result


@_serialized_task_volume_write
def run_split_components_task(
    task, target_label: int, *, size_threshold: int = 100,
    roi_only: bool = False,
) -> dict:
    """Split ``target_label`` into 3D connected components on the working
    label copy — Cellable's ``split_label`` port. Same staging rule as
    :func:`run_watershed_task`: reads/writes the working copy only.
    """
    import numpy as np

    from .cellable_port.split_components import (
        SplitComponentsError,
        run_split_components_3d,
    )
    from .label_paths import working_label_rel_path
    from .visualization.slice_io import resolve_path

    volume = task.volume
    working_path = resolve_path(working_label_rel_path(volume))
    if not working_path.exists():
        raise ValueError("Nothing has been painted for this volume yet.")
    from .visualization.slice_io import read_label_array

    label_mask = read_label_array(working_path)
    original = np.array(label_mask, copy=True) if roi_only else None
    try:
        result = run_split_components_3d(
            label_mask, target_label, size_threshold=size_threshold
        )
    except SplitComponentsError as exc:
        raise ValueError(str(exc)) from exc
    if roi_only:
        from .region_mask import protect_volume_outside_roi

        protect_volume_outside_roi(volume, original, label_mask)
    _save_label_volume(volume, label_mask)

    from .cellable_port.label_state import LabelOrigin

    store, meta_path = _load_label_metadata_store(volume)
    store.mark_edited(result["target_label"], default_origin=LabelOrigin.SPLIT)
    for new_id in result["new_label_ids"]:
        store.create_proposed(new_id, LabelOrigin.SPLIT)
    _save_label_metadata_store(store, meta_path)

    return result


def _plane_axis_names(axis: str) -> tuple[str, str]:
    """The two whole-volume axes a slice plane spans, in array order.

    ``z`` slices are ``(y, x)`` planes, ``y`` slices are ``(z, x)``, ``x``
    slices are ``(z, y)``. Used to pick the *physical* spacing of the plane
    an interpolation runs in, so anisotropy is handled by the distance
    metric rather than by scaling the answer afterwards.
    """
    return {"z": ("y", "x"), "y": ("z", "x"), "x": ("z", "y")}[axis]


def _plane_spacing(volume, axis: str) -> tuple[float, float]:
    """Physical voxel spacing of ``axis``'s slice plane, in array order."""
    z, y, x = _render_voxel_size(volume)
    by_name = {"z": z, "y": y, "x": x}
    first, second = _plane_axis_names(axis)
    return (float(by_name[first]), float(by_name[second]))


def _read_axis_slice(array, axis: str, index: int):
    """One 2-D plane of a ``(Z, Y, X)`` array along ``axis``, as a copy."""
    import numpy as np

    if axis == "z":
        return np.asarray(array[index]).copy()
    if axis == "y":
        return np.asarray(array[:, index, :]).copy()
    return np.asarray(array[:, :, index]).copy()


def _write_axis_slice(array, axis: str, index: int, plane) -> None:
    if axis == "z":
        array[index] = plane
    elif axis == "y":
        array[:, index, :] = plane
    else:
        array[:, :, index] = plane


class _LazyPlanLabels:
    """Bounded-memory label reader with the browser's pending planes overlaid.

    The previous plan path converted the entire working TIFF/HDF5 to int32 and
    copied it before even locating the target label.  On real volumes that was
    several gigabytes per request.  This reader keeps the source lazy and owns
    only decoded pending planes plus the one plane currently being inspected.
    """

    def __init__(self, task, axis: str, pending_slices=None):
        from .label_paths import working_label_rel_path
        from .visualization.slice_io import (
            AXES,
            _open_volume,
            decode_label_rle,
            open_label_volume_readonly,
            resolve_path,
        )

        if axis not in AXES:
            raise ValueError(f"Unknown axis {axis!r}; expected one of z, y, x.")
        if not task.volume.image_location:
            raise ValueError("Volume has no image.")
        image = _open_volume(resolve_path(task.volume.image_location))
        # `_open_volume` owns and LRU-caches this handle (including HDF5
        # views). A request releases its reference, never closes the shared
        # object out from under the next slice/plan request.
        self.shape = tuple(int(value) for value in image.shape)
        image = None
        self.axis = axis
        self.source = None
        candidates = [resolve_path(working_label_rel_path(task.volume))]
        if task.volume.label_location:
            candidates.append(resolve_path(task.volume.label_location))
        for path in candidates:
            if not path.exists():
                continue
            try:
                source = open_label_volume_readonly(path)
                if tuple(int(value) for value in source.shape) != self.shape:
                    continue
                self.source = source
                break
            except Exception:
                continue
        axis_i = AXES[axis]
        plane_shape = tuple(
            self.shape[i] for i in range(3) if i != axis_i
        )
        self.pending = {}
        for raw in pending_slices or []:
            index = int(raw.get("index", -1))
            if index < 0 or index >= self.shape[axis_i]:
                raise ValueError(
                    f"Pending index {index} is out of range for axis {axis!r}."
                )
            shape = tuple(int(value) for value in raw.get("shape", []))
            if shape != plane_shape:
                raise ValueError(
                    f"Pending slice shape {shape} does not match {plane_shape}."
                )
            self.pending[index] = decode_label_rle(
                raw.get("runs", []), shape
            ).astype("int32", copy=False)

    def close(self):
        # Handle lifetime belongs to slice_io's bounded LRU cache.
        self.source = None

    def read_axis(self, axis: str, index: int):
        import numpy as np

        if axis == self.axis and index in self.pending:
            return self.pending[index].copy()
        if self.source is None:
            z, y, x = self.shape
            shape = {"z": (y, x), "y": (z, x), "x": (z, y)}[axis]
            plane = np.zeros(shape, dtype=np.int32)
        else:
            plane = _read_axis_slice(self.source, axis, index).astype(
                np.int32, copy=False
            )
        # A z-scan must still see pending coronal/sagittal planes. Overlay the
        # relevant row/column without constructing a 3-D pending volume.
        if axis == "z" and self.axis == "y":
            for y, pending in self.pending.items():
                plane[y, :] = pending[index, :]
        elif axis == "z" and self.axis == "x":
            for x, pending in self.pending.items():
                plane[:, x] = pending[index, :]
        return plane


def _encode_planned_plane(index: int, before, after) -> dict | None:
    import numpy as np
    from .visualization.slice_io import encode_label_rle

    if np.array_equal(before, after):
        return None
    return {
        "index": int(index),
        "shape": [int(value) for value in after.shape],
        "before_runs": encode_label_rle(before),
        "runs": encode_label_rle(after),
    }


def _scan_label_bbox(
    reader: _LazyPlanLabels,
    target_label: int,
    padding: int = 0,
    *,
    collect_label_ids: bool = False,
):
    """Find a target bbox and global max with one bounded z-plane scan.

    ``collect_label_ids`` additionally returns every non-zero id present, which
    Watershed uses to allocate new ids into gaps. It is off by default because
    it costs an ``np.unique`` sort per plane: Split needs only the bbox and the
    max, and was paying for a whole-volume pass it then discarded — measured at
    ~2.6 s for a 128x1024x1024 label volume.
    """
    import numpy as np

    z0, y0, x0 = reader.shape
    z1 = y1 = x1 = -1
    max_label = 0
    used_labels: set[int] = set()
    target = int(target_label)
    for z in range(reader.shape[0]):
        plane = reader.read_axis("z", z)
        if plane.size:
            max_label = max(max_label, int(plane.max()))
            if collect_label_ids:
                used_labels.update(int(value) for value in np.unique(plane) if value > 0)
        ys, xs = np.nonzero(plane == target)
        if ys.size == 0:
            continue
        z0 = min(z0, z)
        z1 = max(z1, z)
        y0 = min(y0, int(ys.min()))
        y1 = max(y1, int(ys.max()))
        x0 = min(x0, int(xs.min()))
        x1 = max(x1, int(xs.max()))
    if z1 < 0:
        return None, max_label, used_labels
    p = max(0, int(padding))
    return (
        max(0, z0 - p), min(reader.shape[0], z1 + p + 1),
        max(0, y0 - p), min(reader.shape[1], y1 + p + 1),
        max(0, x0 - p), min(reader.shape[2], x1 + p + 1),
    ), max_label, used_labels


def _load_label_crop(reader: _LazyPlanLabels, bbox):
    import os
    import numpy as np

    z1, z2, y1, y2, x1, x2 = bbox
    crop_shape = (z2 - z1, y2 - y1, x2 - x1)
    voxels = int(np.prod(crop_shape, dtype=np.int64))
    limit = int(os.environ.get("MITO_TOOL_PLAN_MAX_VOXELS", "32000000"))
    if voxels > limit:
        dims = "×".join(str(value) for value in crop_shape)
        raise ValueError(
            f"Target label spans {dims} (Z×Y×X), about {voxels:,} voxels; "
            f"the bounded tool limit is {limit:,}. Place seeds on a smaller "
            "object or narrow the crop/ROI before retrying."
        )
    crop = np.empty(crop_shape, dtype=np.int32)
    for z in range(z1, z2):
        crop[z - z1] = reader.read_axis("z", z)[y1:y2, x1:x2]
    return crop


def _seed_local_bbox(reader: _LazyPlanLabels, target_label: int, seeds_zyx, padding: int):
    """Bound an oversized watershed plan around its seeds, not a reused id.

    A label id may legitimately occur in distant disconnected objects.  Its
    global AABB can therefore be enormous while the seeded object is small.
    Watershed only needs the seeded neighbourhood: keep a padded box around
    the seeds and validate that every seed actually lands on the requested
    target, preventing a stale/wrong target id from producing a misleading
    crop or relabelling unrelated voxels.
    """
    if not seeds_zyx:
        raise ValueError("Place at least one seed on the target label.")
    clean = []
    for raw_z, raw_y, raw_x in seeds_zyx:
        z, y, x = int(raw_z), int(raw_y), int(raw_x)
        if not (0 <= z < reader.shape[0] and 0 <= y < reader.shape[1] and 0 <= x < reader.shape[2]):
            raise ValueError(f"Seed ({z}, {y}, {x}) is outside the label volume.")
        if int(reader.read_axis("z", z)[y, x]) != int(target_label):
            raise ValueError(
                f"Seed ({z}, {y}, {x}) is not on target label {target_label}."
            )
        clean.append((z, y, x))
    p = max(1, int(padding))
    zs, ys, xs = zip(*clean)
    return (
        max(0, min(zs) - p), min(reader.shape[0], max(zs) + p + 1),
        max(0, min(ys) - p), min(reader.shape[1], max(ys) + p + 1),
        max(0, min(xs) - p), min(reader.shape[2], max(xs) + p + 1),
    )


def _planned_crop_slices(reader: _LazyPlanLabels, axis: str, bbox, crop):
    z1, z2, y1, y2, x1, x2 = bbox
    answer = []
    ranges = {"z": range(z1, z2), "y": range(y1, y2), "x": range(x1, x2)}
    for index in ranges[axis]:
        before = reader.read_axis(axis, index)
        after = before.copy()
        if axis == "z":
            after[y1:y2, x1:x2] = crop[index - z1]
        elif axis == "y":
            after[z1:z2, x1:x2] = crop[:, index - y1, :]
        else:
            after[z1:z2, y1:y2] = crop[:, :, index - x1]
        planned = _encode_planned_plane(index, before, after)
        if planned is not None:
            answer.append(planned)
    return answer


def plan_watershed_task(
    task, target_label: int, seeds_zyx, *, axis: str = "z", pending_slices=None,
    padding: int = 5,
) -> dict:
    """Compute Watershed against saved + pending labels and return planes only."""
    from .cellable_port.watershed import WatershedError, run_watershed_3d

    reader = _LazyPlanLabels(task, axis, pending_slices)
    _assert_labels_unverified(task.volume, [target_label])
    try:
        try:
            bbox, max_label, used_labels = _scan_label_bbox(
                reader, target_label, padding=padding, collect_label_ids=True
            )
            if bbox is None:
                raise ValueError(f"Label {target_label} not found in the volume.")
            try:
                labels = _load_label_crop(reader, bbox)
            except ValueError as exc:
                # A visually small object can share its id with a distant
                # component, making only the global AABB unsafe.  Restrict
                # watershed to the explicitly seeded neighbourhood; the split
                # tool (which has no seeds) correctly keeps refusing the same
                # oversized global crop.
                if "bounded tool limit" not in str(exc):
                    raise
                bbox = _seed_local_bbox(
                    reader, target_label, seeds_zyx, padding=padding
                )
                labels = _load_label_crop(reader, bbox)
            z1, _z2, y1, _y2, x1, _x2 = bbox
            local_seeds = [(z - z1, y - y1, x - x1) for z, y, x in seeds_zyx]
            result = run_watershed_3d(
                labels,
                target_label,
                local_seeds,
                padding=padding,
                max_existing_label=max_label,
                existing_label_ids=used_labels,
            )
        except WatershedError as exc:
            raise ValueError(str(exc)) from exc
        local_bbox = result["bbox"]
        result["bbox"] = [
            local_bbox[0] + bbox[0], local_bbox[1] + bbox[0],
            local_bbox[2] + bbox[2], local_bbox[3] + bbox[2],
            local_bbox[4] + bbox[4], local_bbox[5] + bbox[4],
        ]
        slices = _planned_crop_slices(reader, axis, bbox, labels)
        return {**result, "axis": axis, "slices": slices}
    finally:
        reader.close()


def plan_split_components_task(
    task, target_label: int, *, axis: str = "z", pending_slices=None,
    size_threshold: int = 100,
) -> dict:
    """Compute connected-component Split without changing the working copy."""
    from .cellable_port.split_components import SplitComponentsError, run_split_components_3d

    reader = _LazyPlanLabels(task, axis, pending_slices)
    _assert_labels_unverified(task.volume, [target_label])
    try:
        try:
            bbox, max_label, _unused_ids = _scan_label_bbox(reader, target_label)
            if bbox is None:
                raise ValueError(f"Label {target_label} not found in the volume.")
            labels = _load_label_crop(reader, bbox)
            result = run_split_components_3d(
                labels,
                target_label,
                size_threshold=size_threshold,
                max_existing_label=max_label,
            )
        except SplitComponentsError as exc:
            raise ValueError(str(exc)) from exc
        local_bbox = result["bbox"]
        result["bbox"] = [
            local_bbox[0] + bbox[0], local_bbox[1] + bbox[0],
            local_bbox[2] + bbox[2], local_bbox[3] + bbox[2],
            local_bbox[4] + bbox[4], local_bbox[5] + bbox[4],
        ]
        slices = _planned_crop_slices(reader, axis, bbox, labels)
        return {**result, "axis": axis, "slices": slices}
    finally:
        reader.close()


def plan_merge_labels_task(
    task, label_a: int, label_b: int, *, axis: str = "z", pending_slices=None,
) -> dict:
    """Compute Merge and return every changed-axis plane, never persist it."""
    import numpy as np

    a, b = int(label_a), int(label_b)
    if a < 1 or b < 1:
        raise ValueError("Both label ids must be positive integers.")
    if a == b:
        raise ValueError("The two label ids must be different.")
    _assert_labels_unverified(task.volume, [a, b])
    kept, removed = min(a, b), max(a, b)
    reader = _LazyPlanLabels(task, axis, pending_slices)
    slices = []
    kept_voxels = removed_voxels = 0
    try:
        axis_len = reader.shape[{"z": 0, "y": 1, "x": 2}[axis]]
        for index in range(axis_len):
            before = reader.read_axis(axis, index)
            kept_voxels += int(np.count_nonzero(before == kept))
            removed_mask = before == removed
            count = int(np.count_nonzero(removed_mask))
            removed_voxels += count
            if count == 0:
                continue
            after = before.copy()
            after[removed_mask] = kept
            slices.append(_encode_planned_plane(index, before, after))
    finally:
        reader.close()
    if removed_voxels == 0:
        raise ValueError(f"Label {removed} is not present in the volume.")
    if kept_voxels == 0:
        raise ValueError(f"Label {kept} is not present in the volume.")
    return {
        "kept_label": kept,
        "removed_label": removed,
        "voxels_merged": removed_voxels,
        "axis": axis,
        "slices": slices,
    }


def plan_delete_label_task(
    task, label_id: int, *, axis: str = "z", pending_slices=None,
) -> dict:
    """Return planes with ``label_id`` cleared; lifecycle metadata is untouched."""
    import numpy as np

    label_id = int(label_id)
    if label_id < 1:
        raise ValueError("label_id must be positive.")
    _assert_labels_unverified(task.volume, [label_id])
    reader = _LazyPlanLabels(task, axis, pending_slices)
    slices = []
    voxels = 0
    try:
        axis_len = reader.shape[{"z": 0, "y": 1, "x": 2}[axis]]
        for index in range(axis_len):
            before = reader.read_axis(axis, index)
            hit = before == label_id
            count = int(np.count_nonzero(hit))
            voxels += count
            if count == 0:
                continue
            after = before.copy()
            after[hit] = 0
            slices.append(_encode_planned_plane(index, before, after))
    finally:
        reader.close()
    if voxels == 0:
        raise ValueError(f"Label {label_id} is not present in the volume.")
    return {
        "label_id": label_id,
        "voxels_deleted": voxels,
        "axis": axis,
        "slices": slices,
    }


def _interpolation_endpoints(volume, axis: str, first_index: int, last_index: int):
    """Validate the endpoint pair and return ``(mm, owned_rel, first, last)``.

    ``first``/``last`` are copies, so the caller can plan against them without
    holding a memmap view open across the (potentially slow) SDF computation.
    """
    from .visualization.slice_io import AXES, _open_volume, resolve_path

    if axis not in AXES:
        raise ValueError(f"Unknown axis {axis!r}; expected one of z, y, x.")
    if not volume.image_location:
        raise ValueError("Volume has no image.")
    image = _open_volume(resolve_path(volume.image_location))
    mm, owned_rel = _writable_label(volume, image.shape)
    n = mm.shape[AXES[axis]]
    lo, hi = int(first_index), int(last_index)
    if lo > hi:
        lo, hi = hi, lo
    if lo < 0 or hi >= n:
        raise ValueError(
            f"Slices {lo} and {hi} are out of range for axis {axis!r} (0..{n - 1})."
        )
    return mm, owned_rel, lo, hi, _read_axis_slice(mm, axis, lo), _read_axis_slice(mm, axis, hi)


def plan_task_interpolation(
    task, *, axis: str, first_index: int, last_index: int, label_id: int,
    overwrite_mode: str | None = None, roi_only: bool = False,
    first_labels=None, last_labels=None,
) -> dict:
    """Preview half of WK-style interpolation (ADR-006 Conflict A).

    Computes what would be written between two slices the annotator has
    already painted the active label on, and **writes nothing** — the
    intermediate masks come back RLE-encoded so the canvas can render them
    and the user can still cancel. The intermediates are 0/1 masks in the
    same wire shape :func:`predict_ai_mask` uses, since a boolean mask is
    just a label slice with two possible values.

    Reads the *working* label copy, not the official one: interpolation
    refines an instance the annotator is actively painting, so it must see
    already-painted-but-not-yet-approved pixels (same rule as Seeds/Split).

    Optional ``first_labels`` / ``last_labels`` (2-D arrays) let the client
    plan against *unsaved* endpoint edits still held in the browser. When
    omitted, endpoints are read from the on-disk working copy.
    """
    from .interpolation import core, service as interp
    from .visualization.slice_io import encode_label_rle

    volume = task.volume
    _assert_labels_unverified(volume, [label_id])
    _mm, _rel, lo, hi, disk_first, disk_last = _interpolation_endpoints(
        volume, axis, first_index, last_index
    )
    first = disk_first if first_labels is None else first_labels
    last = disk_last if last_labels is None else last_labels
    plan = interp.plan_interpolation(
        first_labels=first, last_labels=last, label_id=int(label_id),
        depth=hi - lo, spacing=_plane_spacing(volume, axis),
        overwrite_mode=overwrite_mode or core.OVERWRITE_EMPTY,
    )
    masks = plan.masks
    if roi_only:
        from .region_mask import region_mask_slice

        masks = {
            offset: mask
            & region_mask_slice(volume, axis, lo + offset, mask.shape)
            for offset, mask in masks.items()
        }
    shape = [int(first.shape[0]), int(first.shape[1])]
    return {
        "axis": axis,
        "first_index": lo,
        "last_index": hi,
        "label": int(plan.label_id),
        "depth": int(plan.depth),
        "spacing": list(plan.spacing),
        "overwrite_mode": plan.overwrite_mode,
        "algorithm": plan.algorithm,
        "algorithm_version": plan.algorithm_version,
        "voxels_changed": int(sum(mask.sum() for mask in masks.values())),
        "slices": [
            {
                "index": lo + offset,
                "shape": shape,
                "runs": encode_label_rle(masks[offset].astype("uint8")),
            }
            for offset in plan.offsets
        ],
    }


@_serialized_task_volume_write
def apply_task_interpolation(
    task, actor, *, axis: str, first_index: int, last_index: int, label_id: int,
    overwrite_mode: str | None = None, idempotency_key: str = "",
    expected_version: int | None = None,
    roi_only: bool = False,
) -> dict:
    """Commit an interpolation as **one** undoable annotation operation.

    The plan is recomputed here rather than being carried back from the
    preview: a client-supplied set of masks would let a caller write voxels
    the algorithm never produced, and the endpoints may have been repainted
    since the preview was taken. Recomputing is also what makes the recorded
    operation's payload an honest description of what was written.

    Same staging rule as Seeds/Split/Merge: only the volume's *working* label
    copy is ever touched, never ``volume.label_path``.
    """
    from .cellable_port import labels_3d as _labels_3d
    from .cellable_port.label_state import LabelOrigin
    from .interpolation import core, service as interp
    from .visualization import slice_io
    from .visualization.slice_io import resolve_path

    volume = task.volume
    _assert_labels_unverified(volume, [label_id])
    mm, owned_rel, lo, hi, first, last = _interpolation_endpoints(
        volume, axis, first_index, last_index
    )
    mode = overwrite_mode or core.OVERWRITE_EMPTY
    plan = interp.plan_interpolation(
        first_labels=first, last_labels=last, label_id=int(label_id),
        depth=hi - lo, spacing=_plane_spacing(volume, axis), overwrite_mode=mode,
    )
    protected_ids = _verified_label_ids(volume)

    # Validate the complete operation before its first filesystem write, so a
    # conflict on a later plane cannot leave an earlier plane partially saved.
    for offset, mask in plan.masks.items():
        current = _read_axis_slice(mm, axis, lo + int(offset))
        write_mask = mask
        if roi_only:
            from .region_mask import region_mask_slice

            write_mask = mask & region_mask_slice(
                volume, axis, lo + int(offset), mask.shape
            )
        updated = core.apply_to_slice(
            current, write_mask, label_id=int(plan.label_id), overwrite_mode=mode
        )
        _assert_verified_labels_unchanged(
            volume, current, updated, protected=protected_ids
        )

    owned_path = resolve_path(owned_rel)
    written: list[int] = []
    highest = 0

    def write_slice(offset, mask):
        index = lo + int(offset)
        current = _read_axis_slice(mm, axis, index)
        write_mask = mask
        if roi_only:
            from .region_mask import region_mask_slice

            write_mask = mask & region_mask_slice(
                volume, axis, index, mask.shape
            )
        updated = core.apply_to_slice(
            current, write_mask, label_id=int(plan.label_id), overwrite_mode=mode
        )
        _write_axis_slice(mm, axis, index, updated.astype(mm.dtype))
        written.append(index)
        nonlocal highest
        highest = max(highest, int(updated.max()) if updated.size else 0)

    # `apply_interpolation` owns the transaction, the lock check, idempotency
    # and the single operation row; everything above is how *this* app stores
    # labels, which the interpolation service deliberately does not know.
    operation = interp.apply_interpolation(
        task=task, actor=actor, plan=plan, axis=axis,
        first_index=lo, last_index=hi, write_slice=write_slice,
        idempotency_key=idempotency_key, expected_version=expected_version,
    )

    if written:
        mm.flush()
        # One interpolation touches up to 99 slices, so the incremental
        # per-slice summary update `set_label_slice_ids` uses would cost more
        # than the rescan it avoids. Drop the cache and let the next Labels
        # read rebuild it once.
        _labels_3d.forget_summary(owned_path)
        slice_io.bump_label_max_id(owned_path, mm, highest)
        slice_io.invalidate_read_caches(owned_path)

        store, meta_path = _load_label_metadata_store(volume)
        store.mark_edited(int(plan.label_id), default_origin=LabelOrigin.MANUAL)
        _save_label_metadata_store(store, meta_path)

    return {
        "operation_id": str(operation.id),
        "seq": int(operation.seq),
        "axis": axis,
        "first_index": lo,
        "last_index": hi,
        "label": int(plan.label_id),
        "depth": int(plan.depth),
        "overwrite_mode": mode,
        "voxels_changed": int(plan.voxels_changed),
        "slices_written": sorted(written),
    }


def _task_flood_plan(
    task, *, axis: str, index: int, row: int, col: int, label_id: int,
    overwrite_mode: str | None = None, depth: int = 1, roi_only: bool = False,
):
    """Load the displayed label plane (or a bounded z slab) and plan a fill."""
    import numpy as np

    from .tools.common import BoundingBox, ToolError
    from .tools.service import plan_flood_fill
    from .tools.overwrite import DEFAULT_OVERWRITE_MODE
    from .visualization.slice_io import AXES, _open_volume, resolve_path

    if axis not in AXES:
        raise ToolError(f"Unknown axis {axis!r}.", reason="bad_axis")
    volume = task.volume
    _assert_labels_unverified(volume, [label_id])
    if not volume.image_location:
        raise ToolError("Volume has no image.", reason="missing_image")
    image = _open_volume(resolve_path(volume.image_location))
    mm, owned_rel = _writable_label(volume, image.shape)
    axis_len = mm.shape[AXES[axis]]
    if index < 0 or index >= axis_len:
        raise ToolError(f"Slice {index} is outside 0..{axis_len - 1}.", reason="seed_out_of_bounds")
    depth = max(1, int(depth))
    if depth > 1 and axis != "z":
        raise ToolError("3-D flood fill is available from the z view; use depth 1 on x/y.", reason="bad_depth_axis")

    if depth == 1:
        plane = np.asarray(_read_axis_slice(mm, axis, index))
        h, w = plane.shape
        bbox = BoundingBox(0, 0, 0, 1, h, w)
        plan = plan_flood_fill(
            block=plane[np.newaxis, ...], seed=(0, int(row), int(col)),
            label_id=int(label_id), bbox=bbox,
            overwrite_mode=overwrite_mode or DEFAULT_OVERWRITE_MODE,
        )
        slices = [(index, plan.masks[0])]
        if roi_only:
            from .region_mask import region_mask_slice

            slices = [
                (index, slices[0][1] & region_mask_slice(volume, axis, index, slices[0][1].shape))
            ]
        return mm, owned_rel, plan, slices

    z0 = max(0, int(index) - depth // 2)
    z1 = min(mm.shape[0], z0 + depth)
    z0 = max(0, z1 - depth)
    block = np.asarray(mm[z0:z1])
    h, w = block.shape[1:]
    bbox = BoundingBox(z0, 0, 0, z1, h, w)
    plan = plan_flood_fill(
        block=block, seed=(int(index) - z0, int(row), int(col)),
        label_id=int(label_id), bbox=bbox,
        overwrite_mode=overwrite_mode or DEFAULT_OVERWRITE_MODE,
    )
    slices = [(z0 + offset, mask) for offset, mask in plan.masks.items()]
    if roi_only:
        from .region_mask import region_mask_slice

        slices = [
            (absolute_index, mask & region_mask_slice(volume, axis, absolute_index, mask.shape))
            for absolute_index, mask in slices
        ]
    return mm, owned_rel, plan, slices


def plan_task_flood_fill(task, **kwargs) -> dict:
    """Plan a 2-D/limited-3-D flood fill without writing label voxels."""
    from .visualization.slice_io import encode_label_rle

    _mm, _rel, plan, slices = _task_flood_plan(task, **kwargs)
    return {
        "tool": plan.tool,
        "label": int(plan.label_id),
        "overwrite_mode": plan.overwrite_mode,
        "bbox": plan.bbox.as_list(),
        "voxels_changed": int(sum(mask.sum() for _index, mask in slices)),
        "warnings": plan.warnings,
        "slices": [
            {
                "index": int(index),
                "shape": [int(mask.shape[0]), int(mask.shape[1])],
                "runs": encode_label_rle(mask.astype("uint8")),
            }
            for index, mask in slices
        ],
    }


@_serialized_task_volume_write
def apply_task_flood_fill(task, actor, *, idempotency_key: str = "", **kwargs) -> dict:
    """Apply a planned fill and append exactly one AnnotationOperation."""
    import numpy as np

    from .cellable_port import labels_3d as _labels_3d
    from .cellable_port.label_state import LabelOrigin
    from .tools.service import apply_tool
    from .visualization import slice_io
    from .visualization.slice_io import resolve_path

    mm, owned_rel, plan, slices = _task_flood_plan(task, **kwargs)
    axis = kwargs.get("axis", "z")
    owned_path = resolve_path(owned_rel)
    written = []
    highest = 0
    protected_ids = _verified_label_ids(task.volume)

    for absolute_index, absolute_mask in slices:
        current = np.asarray(_read_axis_slice(mm, axis, absolute_index)).copy()
        updated = current.copy()
        updated[absolute_mask] = int(plan.label_id)
        _assert_verified_labels_unchanged(
            task.volume, current, updated, protected=protected_ids
        )

    def write_slice(offset, mask):
        nonlocal highest
        absolute_index, absolute_mask = slices[int(offset)]
        current = np.asarray(_read_axis_slice(mm, axis, absolute_index)).copy()
        current[absolute_mask] = int(plan.label_id)
        _write_axis_slice(mm, axis, absolute_index, current.astype(mm.dtype))
        written.append(int(absolute_index))
        highest = max(highest, int(current.max()) if current.size else 0)

    operation = apply_tool(
        task=task, actor=actor, plan=plan, write_slice=write_slice,
        idempotency_key=idempotency_key,
    )
    if written:
        mm.flush()
        _labels_3d.forget_summary(owned_path)
        slice_io.bump_label_max_id(owned_path, mm, highest)
        slice_io.invalidate_read_caches(owned_path)
        store, meta_path = _load_label_metadata_store(task.volume)
        store.mark_edited(int(plan.label_id), default_origin=LabelOrigin.MANUAL)
        _save_label_metadata_store(store, meta_path)
    return {
        "operation_id": str(operation.id),
        "seq": int(operation.seq),
        "tool": plan.tool,
        "label": int(plan.label_id),
        "overwrite_mode": plan.overwrite_mode,
        "bbox": plan.bbox.as_list(),
        "voxels_changed": int(sum(mask.sum() for _index, mask in slices)),
        "slices_written": sorted(set(written)),
        "warnings": plan.warnings,
    }


@_serialized_task_volume_write
def run_merge_labels_task(
    task, label_a: int, label_b: int, *, roi_only: bool = False
) -> dict:
    """Merge two labels on the working copy — always keeps the *smaller* id.
    Same staging rule as :func:`run_watershed_task`.
    """
    import numpy as np

    from .cellable_port.merge_labels import MergeLabelsError, run_merge_labels
    from .label_paths import working_label_rel_path
    from .visualization.slice_io import resolve_path

    volume = task.volume
    working_path = resolve_path(working_label_rel_path(volume))
    if not working_path.exists():
        raise ValueError("Nothing has been painted for this volume yet.")
    from .visualization.slice_io import read_label_array

    label_mask = read_label_array(working_path)
    original = np.array(label_mask, copy=True) if roi_only else None
    try:
        result = run_merge_labels(label_mask, label_a, label_b)
    except MergeLabelsError as exc:
        raise ValueError(str(exc)) from exc
    if roi_only:
        from .region_mask import protect_volume_outside_roi

        protect_volume_outside_roi(volume, original, label_mask)
    _save_label_volume(volume, label_mask)

    from .cellable_port.label_state import LabelOrigin

    store, meta_path = _load_label_metadata_store(volume)
    store.mark_edited(result["kept_label"], default_origin=LabelOrigin.MANUAL)
    # ROI-only merge can deliberately leave the absorbed id outside ROI.
    if not np.any(label_mask == result["removed_label"]):
        store.remove(result["removed_label"])
    _save_label_metadata_store(store, meta_path)

    return result


# --- Labels panel ("All" scope) + 3D labels preview -------------------------

def get_labels_summary(volume, *, readonly: bool = False) -> dict:
    """Per-instance-id voxel count + first/last z + lifecycle state across
    the volume's whole *working* label copy — backs the Labels panel's
    "All labels" scope (search across the whole volume, not just the
    current slice), jump-to-slice, and the Filters Options surface (Show
    state filter, Hide Verified, sort by state, state legend counts). See
    ``cellable_port/labels_3d.py`` for the voxel-count/z-range half (cached,
    since it's an O(volume) scan) and ``cellable_port/label_state.py`` for
    the lifecycle-state half (the JSON sidecar, cheap to load every call).

    An id with no tracked metadata (e.g. pre-existing real data forked from
    an externally-produced official label, never explicitly touched by any
    of this app's own AI/watershed/tracking/paint paths) defaults to
    ``state="proposed", origin="unknown"`` — the same safe "needs a human
    look" default Cellable's own ``LabelMetadata`` falls back to.

    Ensures the working copy exists (seeded from the official label on first
    touch) before scanning — same as :func:`get_label_slice_ids`. Otherwise
    a Labels-summary request that races ahead of the first slice load would
    see no file and return an empty "All" list while "This slice" already
    shows forked ids.
    """
    from .cellable_port.label_state import LabelState
    from .cellable_port.labels_3d import label_summary

    path = _visible_label_path(volume) if readonly else _working_label_path_for_read(volume)
    if path is None:
        return {"labels": [], "stats": {"total": 0, "proposed": 0, "edited": 0, "verified": 0}}
    summary = label_summary(path)
    store, _ = _load_label_metadata_store(volume)

    stats = {"total": 0, "proposed": 0, "edited": 0, "verified": 0}
    rows = []
    for row in summary["labels"]:
        meta = store.get(row["id"])
        state = meta.state.value if meta else LabelState.PROPOSED.value
        origin = meta.origin.value if meta else "unknown"
        stats["total"] += 1
        stats[state] += 1
        rows.append(
            {
                **row,
                "state": state,
                "origin": origin,
                "verified_at": meta.verified_at if meta else "",
                "can_revert": bool(meta and meta.has_snapshot()),
            }
        )
    return {"labels": rows, "stats": stats}


@_serialized_task_volume_write
def reset_working_labels_to_registered(task: AnnotationTask) -> dict:
    """Throw away this task's working annotation and re-seed it from the
    volume's *registered* label mask.

    The working copy is a temporary, editable draft (see the module note on
    ``_writable_label``); the registered mask is the immutable thing it was
    forked from. This restores the second over the first, and **never writes to
    the registered source** — it is only ever read, by the same
    :func:`_seed_working_label` the first-touch path uses, so a reset volume is
    byte-for-byte what a freshly opened one would be.

    Everything derived from the discarded draft goes with it, or the app would
    show state describing voxels that no longer exist:

    * the per-label lifecycle sidecar (Proposed/Edited/Verified);
    * the Track prompt queue, any pending Track preview, and its snapshot file;
    * the cached label summary, ROI membership, and slice caches.

    Returns a small report rather than the mask: the caller reloads through the
    ordinary read paths.
    """
    import os

    from core.data_root import assert_owned

    from .cellable_port.labels_3d import forget_summary
    from .label_paths import working_label_metadata_rel_path, working_label_rel_path
    from .region_mask import forget_region_label_ids
    from .visualization import slice_io
    from .visualization.slice_io import _open_volume, resolve_path

    volume = task.volume
    if not volume.image_location:
        raise ValueError("Volume has no image, so it has no label shape to reset to.")
    shape = tuple(int(v) for v in _open_volume(resolve_path(volume.image_location)).shape)

    working_rel = working_label_rel_path(volume)
    working_path = resolve_path(working_rel)
    source = registered_label_location(volume)
    # Belt and braces: the seeding helper already refuses to read a source that
    # *is* the destination, but a reset is the one caller that deletes the
    # destination first, so a mistake here would delete the source itself.
    if source and resolve_path(source) == working_path:
        raise ValueError(
            "The registered label for this volume resolves to its working copy; "
            "there is nothing distinct to reset to."
        )

    # Drop the queue and pending preview *before* the mask goes, so a crash in
    # between leaves prompts pointing at a mask that is merely stale rather than
    # a pending review pointing at voxels that no longer exist.
    volume_model = volume.__class__
    with transaction.atomic():
        locked = volume_model.objects.select_for_update().get(pk=volume.pk)
        metadata = dict(locked.metadata or {})
        pending = metadata.pop(TRACKING_PENDING_KEY, None)
        metadata[TRACKING_PROMPTS_KEY] = {
            "version": TRACKING_PROMPTS_VERSION,
            "items": [],
        }
        locked.metadata = metadata
        locked.save(update_fields=["metadata"])
        task.volume = locked
        volume = locked
    if isinstance(pending, dict):
        try:
            _delete_tracking_preview_snapshot(str(pending.get("snapshot_path", "")))
        except (OSError, ValueError):
            # A stranded snapshot is wasted disk, not a reason to fail a reset.
            pass

    assert_owned(working_path, what="working label copy")
    slice_io.drop_file(working_path)
    forget_summary(working_path)
    forget_region_label_ids(working_path)
    if working_path.exists():
        working_path.unlink()

    sidecar = resolve_path(working_label_metadata_rel_path(volume))
    assert_owned(sidecar, what="label metadata sidecar")
    for lifecycle_path in (sidecar, sidecar.with_name(f"{sidecar.name}.bak")):
        assert_owned(lifecycle_path, what="label metadata sidecar")
        if lifecycle_path.exists():
            os.remove(lifecycle_path)

    working_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_working_label(volume, working_path, shape, source)
    slice_io.invalidate_read_caches(working_path)
    forget_summary(working_path)
    forget_region_label_ids(working_path)
    return {
        "reset": True,
        "task": task.pk,
        "volume": volume.pk,
        "restored_from": source,
        "seeded_empty": not source,
    }


def get_region_label_ids(volume, *, readonly: bool = False) -> dict:
    """Instance ids that touch this volume's ROI **anywhere in z**.

    Backs "Region only" and the Labels panel's "Hide non-ROI labels": both show
    or hide a whole instance, so both need the volume-wide answer rather than
    the per-plane one the canvas can compute for itself. See
    ``region_mask.region_touching_label_ids`` for why this is affordable.

    ``has_region`` false means "no filtering is possible", which the client must
    not confuse with "nothing touches the region" (an empty ``ids`` list).
    """
    from .region_mask import region_touching_label_ids
    from .visualization.slice_io import resolve_path

    if not volume.region_mask_location:
        return {"has_region": False, "ids": []}
    path = _visible_label_path(volume) if readonly else _working_label_path_for_read(volume)
    if path is None:
        return {"has_region": True, "ids": []}
    ids = region_touching_label_ids(path, resolve_path(volume.region_mask_location))
    return {"has_region": True, "ids": sorted(ids)}


@_serialized_volume_write
def set_label_lifecycle_action(volume, label_id: int, action: str) -> dict:
    """Apply a Cellable-parity lifecycle action to one label: ``"verify"``,
    ``"unverify"`` (VERIFIED -> EDITED), ``"revert"`` (restore the single-
    slice snapshot recorded when an AI-mask-created label was proposed —
    only available when ``can_revert`` was true in :func:`get_labels_summary`),
    or ``"reject"`` (delete every voxel of this label from the working copy
    and drop its metadata). Ported from Cellable's ``verifyLabel``/
    ``unverifyLabel``/``revertLabelToProposed``/``rejectLabel``
    (``app.py``) — the confirm-before-destructive-action UI lives in the
    frontend (per ``progress/history/04-incident-data-safety.md``'s "no
    casual one-click destructive action" rule), not here.
    """
    import numpy as np

    from .label_paths import working_label_rel_path
    from .visualization import slice_io
    from .visualization.slice_io import AXES, _open_volume, decode_label_rle, resolve_path

    store, meta_path = _load_label_metadata_store(volume)
    label_int = int(label_id)

    if action == "verify":
        from .cellable_port.labels_3d import label_summary

        path = _working_label_path_for_read(volume)
        present = path is not None and any(
            int(row["id"]) == label_int for row in label_summary(path)["labels"]
        )
        if not present:
            raise ValueError(
                f"Label {label_int} is not present in the saved working volume. "
                "Save it before verifying."
            )
        meta = store.verify(label_int)
    elif action == "unverify":
        meta = store.unverify(label_int)
        if meta is None:
            raise ValueError(f"Label {label_int} is not currently verified.")
    elif action in ("revert", "reject"):
        # These actions mutate raster geometry too. Keep the direct lifecycle
        # endpoint consistent with brush and tool writes: a verified label can
        # only be reverted or rejected after an explicit Unverify.
        _assert_labels_unverified(volume, [label_int])
        if action == "revert" and not store.can_revert(label_int):
            raise ValueError(f"Label {label_int} has no proposed snapshot to revert to.")
        if not volume.image_location:
            raise ValueError("Volume has no image.")
        image = _open_volume(resolve_path(volume.image_location))
        mm, owned_rel = _writable_label(volume, image.shape)
        mm[mm == label_int] = 0
        if action == "revert":
            meta = store.get(label_int)
            snap = decode_label_rle(meta.snapshot_rle, tuple(meta.snapshot_shape))
            z = meta.snapshot_z
            axis_i = AXES["z"]
            n = mm.shape[axis_i]
            idx = max(0, min(int(z), n - 1))
            current = np.asarray(mm[idx])
            current[snap > 0] = label_int
            mm[idx] = current
            store.revert(label_int)
        else:
            store.remove(label_int)
            meta = None
        mm.flush()
        slice_io.invalidate_read_caches(resolve_path(owned_rel))
        from .cellable_port.labels_3d import forget_summary

        forget_summary(resolve_path(owned_rel))
    else:
        raise ValueError(f"Unknown lifecycle action '{action}'.")

    _save_label_metadata_store(store, meta_path)
    return {
        "label_id": label_int,
        "action": action,
        "state": meta.state.value if meta else None,
        "removed": meta is None,
    }


def _working_label_path_for_read(volume):
    """Path to the volume's working label copy, seeding it from the official
    label if it doesn't exist yet — shared by the whole-volume readers below."""
    from .label_paths import working_label_rel_path
    from .visualization.slice_io import _open_volume, resolve_path

    if not volume.image_location:
        return None
    image = _open_volume(resolve_path(volume.image_location))
    _writable_label(volume, image.shape)  # seed working copy if missing
    return resolve_path(working_label_rel_path(volume))


def get_labels_3d_preview(
    volume, label_ids: list[int], *, readonly: bool = False
) -> dict:
    """Downsampled per-label voxel grids — the *legacy* 3D payload (the panel
    renders :func:`get_labels_3d_mesh` now). See ``cellable_port/labels_3d.py``."""
    from .cellable_port.labels_3d import labels_3d_preview

    path = _visible_label_path(volume) if readonly else _working_label_path_for_read(volume)
    if path is None:
        return {"shape": (0, 0, 0), "grids": {}}
    return labels_3d_preview(path, label_ids)


def get_labels_3d_mesh(
    volume, label_ids: list[int], *, readonly: bool = False
) -> dict:
    """Marching-cubes iso-surface meshes for the 3D Labels panel — one shared
    path for Annotate, task View, and the public hard-case share.

    Adds the volume's physical voxel size (``Volume.voxel_size_*``, defaulting
    to isotropic 1) to what ``cellable_port.labels_3d.labels_3d_mesh``
    returns, so the renderer can scale z correctly on anisotropic EM instead
    of drawing a squashed/stretched blob. The geometry itself stays in voxel
    coordinates (and therefore stays cacheable independent of voxel size).
    """
    from .cellable_port.labels_3d import labels_3d_mesh

    path = _visible_label_path(volume) if readonly else _working_label_path_for_read(volume)
    if path is None:
        return {"origin": (0.0, 0.0, 0.0), "size": (0.0, 0.0, 0.0), "meshes": [], "truncated": 0,
                "voxel_size": (1.0, 1.0, 1.0)}
    result = labels_3d_mesh(path, label_ids)
    result["voxel_size"] = _render_voxel_size(volume)
    return result


# A voxel more than this many times longer on one axis than another is not a
# real EM acquisition, it's a metadata bug (mismatched units, a resolution tag
# that never meant a physical size). Rendering it would squash a label into a
# sheet, so fall back to isotropic instead of trusting it.
_MAX_PLAUSIBLE_ANISOTROPY = 50.0


def _render_voxel_size(volume) -> tuple[float, float, float]:
    """Physical ``(z, y, x)`` voxel size to render this volume's 3D with.

    Prefers what the volume records; falls back to the image headers when the
    record is incomplete (``core.utils.inspect_volume_voxel_size``, cached).
    Only a *complete and plausible* triple is used — a partially filled one
    would scale one axis against an implicit 1, and an implausible one comes
    from bad metadata rather than from a real anisotropic acquisition. Anything
    else renders isotropic, which is wrong-but-harmless rather than unreadable.
    """
    from .visualization.slice_io import resolve_path

    def usable(triple) -> tuple[float, float, float] | None:
        if triple is None:
            return None
        values = [float(v) for v in triple if v and float(v) > 0]
        if len(values) != 3:
            return None
        if max(values) / min(values) > _MAX_PLAUSIBLE_ANISOTROPY:
            return None
        return (values[0], values[1], values[2])

    recorded = usable((volume.voxel_size_z, volume.voxel_size_y, volume.voxel_size_x))
    if recorded is not None:
        return recorded
    if volume.image_location:
        detected = usable(inspect_volume_voxel_size(resolve_path(volume.image_location)))
        if detected is not None:
            return detected
    return (1.0, 1.0, 1.0)


# --- Project hard cases (+ the optional public read-only link) --------------
#
# Permission matrix (see progress/backend/annotation/MODULE.md):
#
#   action      | manager | creator | other project member | token holder
#   ------------+---------+---------+----------------------+--------------
#   view        |   yes   |   yes   |         yes          | yes (if live)
#   annotate    |   yes   |  yes*   |         no           | no
#   take down   |   yes   |   yes   |         no           | no
#
#   * the creator additionally needs live edit access to the underlying task
#     (``can_annotate_task``) — otherwise "Annotate" would be a button that
#     403s, since a case's edits are ordinary edits to the task's working copy.

def create_hard_case(
    *, task: AnnotationTask, user, label_id: int, note: str = ""
) -> HardCase:
    """Record ``label_id`` on ``task`` as a hard case for the whole project.

    Denormalizes the task's project/volume onto the row so the project page and
    the inbox are single indexed queries, and mints the public token (the
    model's ``default`` — unguessable CSPRNG — so nothing here loops for
    uniqueness) for the optional copyable link. Access control on *creating* is
    the caller's job (only someone who can open Annotate should — see the API
    view's gate); this function just records it.
    """
    cleaned_note = (note or "").strip()
    if len(cleaned_note) > 1000:
        raise ValueError("Hard-case notes must be 1,000 characters or fewer.")
    return HardCase.objects.create(
        task=task,
        project=task.project,
        volume=task.volume,
        label_id=int(label_id),
        note=cleaned_note,
        created_by=user,
    )


def get_public_hard_case(token: str) -> HardCase | None:
    """Resolve a live (non-revoked) case by public token, or ``None``. The token
    is the only lookup key on this path — there is deliberately no way to
    enumerate cases without an account. ``resolved`` cases still resolve: taking
    a case down settles it inside the project, it does not break links that were
    already pasted somewhere."""
    if not token:
        return None
    return (
        HardCase.objects.select_related(
            "task", "task__volume", "task__project", "created_by"
        )
        .filter(token=token, revoked=False)
        .first()
    )


def visible_hard_cases(user, *, project=None, volume=None):
    """Cases ``user`` may see, newest first (the inbox reads like email).

    Visibility is project membership (:func:`is_project_member`) — the
    project's manager(s), its requester, and every annotator with a task on
    it. Managers see everything. Both open and resolved cases are returned;
    the client groups them.
    """
    from accounts.roles import is_manager
    from projects.models import Project

    qs = HardCase.objects.select_related(
        "task", "volume", "project", "created_by", "resolved_by"
    ).annotate(message_count_value=Count("messages"))
    if project is not None:
        qs = qs.filter(project=project)
    if volume is not None:
        qs = qs.filter(volume=volume)
    if is_manager(user):
        return qs

    uid = getattr(user, "id", None)
    if uid is None:
        return qs.none()
    # A member of a project sees all of its cases; plus, defensively, anything
    # they created themselves on a project whose membership has since changed.
    member_projects = set(
        Project.objects.filter(
            Q(tasks__assigned_to_id=uid)
            | Q(created_by_id=uid)
            | Q(memberships__user_id=uid)
        ).values_list("id", flat=True)
    )
    from accounts.teams import teams_enabled
    if teams_enabled():
        member_projects.update(
            Project.objects.filter(
                teams__memberships__user_id=uid
            ).values_list("id", flat=True)
        )
    return qs.filter(Q(project_id__in=member_projects) | Q(created_by_id=uid))


def can_view_hard_case(user, case: HardCase) -> bool:
    """Every project member (and the creator) may open a case, View-only."""
    if case.created_by_id and case.created_by_id == getattr(user, "id", None):
        return True
    return is_project_member(user, case.project) or can_view_task(user, case.task)


def can_annotate_hard_case(user, case: HardCase) -> bool:
    """Only the creator or a manager — **and** only while they actually have
    live edit access to the underlying task. A case is not a separate document:
    annotating one paints the task's working copy through the ordinary editor
    endpoints, so a creator whose task was reassigned or locked correctly drops
    to View-only rather than getting a button that 403s."""
    from accounts.roles import is_manager

    if not (is_manager(user) or case.created_by_id == getattr(user, "id", None)):
        return False
    return can_annotate_task(user, case.task)


def can_take_down_hard_case(user, case: HardCase) -> bool:
    """Resolve/reopen/revoke is creator-or-manager, regardless of task access —
    the person who raised a case can always settle it."""
    from accounts.roles import is_manager

    return is_manager(user) or case.created_by_id == getattr(user, "id", None)


def update_hard_case_note(case: HardCase, *, user, note: str) -> HardCase:
    """Replace the primary note; only its creator or a manager may do so."""
    if not can_take_down_hard_case(user, case):
        raise PermissionError("Only the person who recorded this case, or a manager, can edit its note.")
    cleaned = (note or "").strip()
    if len(cleaned) > 1000:
        raise ValueError("Hard-case notes must be 1,000 characters or fewer.")
    case.note = cleaned
    case.save(update_fields=["note"])
    return case


def list_hard_case_messages(case: HardCase):
    """Return the append-only discussion in stable chronological order."""
    return case.messages.select_related("author").order_by("created_at", "id")


def add_hard_case_message(case: HardCase, *, user, body: str) -> HardCaseMessage:
    """Append one non-empty, bounded reply from a case viewer."""
    if not can_view_hard_case(user, case):
        raise PermissionError("You do not have access to discuss this hard case.")
    cleaned = (body or "").strip()
    if not cleaned:
        raise ValueError("Message cannot be blank.")
    if len(cleaned) > 2000:
        raise ValueError("Messages must be 2,000 characters or fewer.")
    return HardCaseMessage.objects.create(hard_case=case, author=user, body=cleaned)


def set_hard_case_status(case: HardCase, *, status: str, user=None) -> HardCase:
    """Take a case down (``resolved``) or put it back on the board (``open``).

    Never deletes: other members keep read access to settled cases, which is
    what makes the inbox a record of what the team has already worked through.
    """
    if status not in HardCaseStatus.values:
        raise ValueError(f"Unknown hard-case status '{status}'.")
    case.status = status
    if status == HardCaseStatus.RESOLVED:
        case.resolved_by = user
        case.resolved_at = timezone.now()
    else:
        case.resolved_by = None
        case.resolved_at = None
    case.save(update_fields=["status", "resolved_by", "resolved_at"])
    return case


def set_hard_case_revoked(case: HardCase, *, revoked: bool) -> HardCase:
    """Kill (or restore) the *public* token link only. In-app members keep
    their access either way — see :func:`visible_hard_cases`."""
    case.revoked = bool(revoked)
    case.save(update_fields=["revoked"])
    return case


# --- Workload --------------------------------------------------------------

def calculate_annotator_workload(project=None) -> list[dict]:
    """Per-annotator task counts (active, submitted, approved, total)."""
    task_qs = AnnotationTask.objects.exclude(assigned_to__isnull=True)
    if project is not None:
        task_qs = task_qs.filter(project=project)

    rows = (
        task_qs.values("assigned_to", "assigned_to__username")
        .annotate(
            total=Count("id"),
            active=Count(
                "id", filter=Q(status__in=ACTIVE_TASK_STATUSES)
            ),
            submitted=Count("id", filter=Q(status=TaskStatus.SUBMITTED)),
            approved=Count("id", filter=Q(status=TaskStatus.APPROVED)),
        )
        .order_by("assigned_to__username")
    )
    return [
        {
            "annotator_id": r["assigned_to"],
            "username": r["assigned_to__username"],
            "total": r["total"],
            "active": r["active"],
            "submitted": r["submitted"],
            "approved": r["approved"],
        }
        for r in rows
    ]
