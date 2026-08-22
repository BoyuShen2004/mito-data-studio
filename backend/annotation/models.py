import secrets
import uuid

from django.conf import settings
from django.db import models

from core.choices import (
    DifficultyLevel,
    HardCaseStatus,
    PriorityLevel,
    QCStatus,
    ReviewDecision,
    SubmissionSource,
    SubmissionReviewStatus,
    TaskStatus,
    TaskType,
)
from core.storage import get_mito_storage


def submission_upload_to(instance, filename):
    return f"submissions/task_{instance.task_id}/{filename}"


class AnnotationTask(models.Model):
    """A frame-based annotation unit covering a z-range of a volume."""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="tasks"
    )
    volume = models.ForeignKey(
        "volumes.Volume", on_delete=models.CASCADE, related_name="tasks"
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="annotation_tasks",
    )

    # Spatial task bounds are 0-based half-open intervals: [start, end).
    # A whole 256-layer volume is therefore z_start=0, z_end=256; callers that
    # need the final stored index use z_end - 1, while UI displays layers 1–256.
    z_start = models.PositiveIntegerField()
    z_end = models.PositiveIntegerField()
    y_start = models.PositiveIntegerField(default=0)
    y_end = models.PositiveIntegerField()
    x_start = models.PositiveIntegerField(default=0)
    x_end = models.PositiveIntegerField()

    task_type = models.CharField(max_length=30, choices=TaskType.choices)
    status = models.CharField(
        max_length=20, choices=TaskStatus.choices, default=TaskStatus.UNASSIGNED
    )
    priority = models.IntegerField(
        choices=PriorityLevel.choices, default=PriorityLevel.NORMAL
    )
    difficulty = models.IntegerField(
        choices=DifficultyLevel.choices, default=DifficultyLevel.MODERATE
    )
    instructions = models.TextField(blank=True)
    deadline = models.DateField(null=True, blank=True)

    # The single gate on "may the annotator still work on this task?" — set
    # when a manager approves *without* ticking "allow further annotation"
    # (see ``services.approve_submission``), cleared on reject / revision.
    # Deliberately not derived from ``status``: an annotator may re-submit
    # from ``submitted`` or ``approved`` as long as this is False, which is
    # exactly what the old status-list gating got wrong (Submit vanished
    # after the first submit). See ``services.can_submit_task``.
    annotation_locked = models.BooleanField(default=False)
    # Total submits across both channels. Submission rows are durable; this is
    # retained as the cheap aggregate used by task-list surfaces.
    submission_count = models.PositiveIntegerField(default=0)

    # Denormalized last review decision. ReviewRecord keeps the full log, but
    # these make "what did the manager say?" a single-row read for the task
    # page, the People panels, and the annotator's dashboard.
    last_decision = models.CharField(
        max_length=20, choices=ReviewDecision.choices, blank=True
    )
    last_decision_at = models.DateTimeField(null=True, blank=True)
    last_decision_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_tasks",
    )
    last_decision_comments = models.TextField(blank=True)
    last_decision_source = models.CharField(
        max_length=10, choices=SubmissionSource.choices, blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["volume_id", "z_start"]

    def __str__(self) -> str:
        return f"Task #{self.pk} {self.volume.name} z[{self.z_start}:{self.z_end}]"

    @property
    def frame_label(self) -> str:
        first = self.z_start + 1
        last = max(first, self.z_end)
        return f"z {first}" if first == last else f"z {first}–{last}"


class AnnotationSubmission(models.Model):
    """A label file submitted by an annotator for a task."""

    task = models.ForeignKey(
        AnnotationTask, on_delete=models.CASCADE, related_name="submissions"
    )
    annotator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
    )
    # Offline rows own the uploaded file. In-app rows own an immutable snapshot
    # copied from the working label at submit time; neither channel points at a
    # mutable working draft while awaiting review.
    label_file = models.FileField(
        storage=get_mito_storage, upload_to=submission_upload_to,
        blank=True, null=True,
    )
    source = models.CharField(
        max_length=10, choices=SubmissionSource.choices, default=SubmissionSource.UPLOAD,
    )
    notes = models.TextField(blank=True)
    qc_status = models.CharField(
        max_length=20, choices=QCStatus.choices, default=QCStatus.NOT_RUN
    )
    qc_report = models.JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    # --- Append-only, per-channel history ----------------------------------
    # A same-channel resubmit marks the prior pending row voided. Online and
    # offline rows coexist until an approval resolves the competition.
    #
    # `superseded_at` is indexed because the hot read is "the current
    # submission for this task" — `superseded_at IS NULL` — which must stay a
    # single indexed lookup no matter how long the history grows.
    superseded_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="Set when a newer submission replaced this one.",
    )
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_by",
        help_text="The submission this one replaced, forming the round chain.",
    )
    review_status = models.CharField(
        max_length=20,
        choices=SubmissionReviewStatus.choices,
        default=SubmissionReviewStatus.PENDING,
        db_index=True,
    )
    superseded_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [
            # Legacy/current lookup aid; source + state below is the dual-
            # channel review-queue index.
            models.Index(
                fields=["task", "superseded_at"], name="idx_submission_current"
            ),
            models.Index(
                fields=["task", "source", "review_status"],
                name="idx_sub_task_source_state",
            ),
        ]

    @property
    def is_current(self) -> bool:
        """Is this the submission a reviewer should be looking at?"""
        return (
            self.superseded_at is None
            and self.review_status == SubmissionReviewStatus.PENDING
        )

    @property
    def round_number(self) -> int:
        """1-based review round, counted by walking the supersedes chain.

        Deliberately a property rather than a stored counter: it cannot drift
        from the chain it describes. Callers rendering long histories should
        prefer ``AnnotationTask.submission_count``, which is O(1).
        """
        n, seen, cur = 1, {self.pk}, self.supersedes
        while cur is not None:
            n += 1
            if cur.pk in seen:  # defensive: a cycle must not hang a page render
                break
            seen.add(cur.pk)
            cur = cur.supersedes
        return n

    def __str__(self) -> str:
        return f"Submission #{self.pk} for task #{self.task_id}"


class AssignmentWithdrawal(models.Model):
    """Immutable history of a manager-withdrawn or transferred assignment.

    The canonical task is returned to ``unassigned`` so it can be handed to a
    new person, or updated in place during a direct transfer. This row preserves
    the former annotator/team for the Done item; it is deliberately separate
    from task status so later reassignment cannot rewrite assignment history.
    """

    class Outcome(models.TextChoices):
        WITHDRAWN = "withdrawn", "Withdrawn"
        TRANSFERRED = "transferred", "Transferred"

    task = models.ForeignKey(
        AnnotationTask, on_delete=models.CASCADE, related_name="withdrawals"
    )
    annotator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="assignment_withdrawals",
    )
    team_name = models.CharField(max_length=255)
    reason = models.CharField(max_length=255, default="Assignment withdrawn")
    outcome = models.CharField(
        max_length=20, choices=Outcome.choices, default=Outcome.WITHDRAWN
    )
    transferred_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignment_transfers_received",
    )
    withdrawn_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-withdrawn_at", "-id"]
        indexes = [
            models.Index(
                fields=["annotator", "-withdrawn_at"],
                name="idx_withdrawal_annotator",
            )
        ]


def _generate_share_token() -> str:
    # 32 bytes → 43 url-safe chars; unguessable, the only thing gating a
    # public share (see HardCaseShare). token_urlsafe is drawn from
    # `secrets`, i.e. the OS CSPRNG.
    return secrets.token_urlsafe(32)


class HardCase(models.Model):
    """A hard case recorded against a project, for a single label instance.

    Created by an annotator/manager from the Annotate view ("Record hard
    case"): captures the **Active** instance id at record time, denormalizes
    the task's ``project``/``volume`` so the project + inbox lists are one
    indexed query, and mints an unguessable ``token``.

    Two audiences, one row:

    * **Project members** — the project's manager(s), every annotator with a
      task on the project, and the project's requester — see the case in
      ``/hard-cases`` and on the project page and open it at
      ``/hard-cases/<id>`` (no token needed). Membership is the source of
      truth for "everyone related to this project can see it"; see
      ``services.can_view_hard_case``.
    * **Anyone with the token URL** — the original public, no-account,
      read-only link (kept for pasting into an email/Slack thread outside the
      app). ``revoked`` kills that link without touching the case's standing
      inside the project.

    ``status`` is the in-project lifecycle: "take down" resolves a case
    (``resolved`` + ``resolved_by``/``resolved_at``) rather than deleting it,
    so members can still read historical cases View-only. Only the creator
    and managers may annotate or take a case down
    (``services.can_annotate_hard_case`` / ``can_take_down_hard_case``).

    The public endpoints (see ``annotation.api`` ``PublicHardCase*`` views)
    only ever *read* the same slice/label data the authed viewer serves. No
    write path accepts a token.
    """

    token = models.CharField(
        max_length=64, unique=True, db_index=True, default=_generate_share_token
    )
    task = models.ForeignKey(
        AnnotationTask, on_delete=models.CASCADE, related_name="hard_cases"
    )
    # Denormalized from ``task`` at creation so listing a project's (or a
    # volume's) cases never has to join through tasks. A case belongs to the
    # project it was found in even if the task is later re-scoped.
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="hard_cases",
        null=True,
        blank=True,
    )
    volume = models.ForeignKey(
        "volumes.Volume",
        on_delete=models.CASCADE,
        related_name="hard_cases",
        null=True,
        blank=True,
    )
    # The Active instance id at record time — the "case" itself. Viewers
    # default to soloing this id (canvas + 3D), but may reveal others.
    label_id = models.PositiveIntegerField()
    note = models.TextField(max_length=1000, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hard_cases",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20, choices=HardCaseStatus.choices, default=HardCaseStatus.OPEN
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_hard_cases",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    # Kill switch for the *public token only* — a revoked token stops
    # resolving (404) while project members keep their in-app access.
    revoked = models.BooleanField(default=False)

    class Meta:
        # Newest first — the inbox reads like email (see the F acceptance row
        # in progress/history/05-submit-people-hardcases.md).
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"HardCase #{self.pk} task #{self.task_id} label {self.label_id}"

    @property
    def path(self) -> str:
        """Public share route (relative; the client prepends origin)."""
        return f"/share/hard-case/{self.token}"

    @property
    def app_path(self) -> str:
        """In-app route for project members (no token)."""
        return f"/hard-cases/{self.pk}"


class HardCaseMessage(models.Model):
    """One append-only discussion reply attached to a hard case."""

    hard_case = models.ForeignKey(
        HardCase, on_delete=models.CASCADE, related_name="messages"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hard_case_messages",
    )
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"HardCaseMessage #{self.pk} on case #{self.hard_case_id}"


class ReviewRecord(models.Model):
    """A manager/reviewer decision on a submission.

    Deliberately outlives the submission it decided. Re-submitting deletes the
    previous ``AnnotationSubmission`` row and its files (latest-only, by
    product rule — see ``services._supersede_submissions``); these rows are
    the thin event log that survives, which is what the People panels read for
    "submissions / approve·reject" per annotator. Hence ``submission`` is
    ``SET_NULL`` and ``task`` is the durable link.
    """

    submission = models.ForeignKey(
        AnnotationSubmission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews",
    )
    task = models.ForeignKey(
        AnnotationTask,
        on_delete=models.CASCADE,
        related_name="reviews",
        null=True,
        blank=True,
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews",
    )
    decision = models.CharField(max_length=20, choices=ReviewDecision.choices)
    source = models.CharField(
        max_length=10, choices=SubmissionSource.choices, blank=True
    )
    comments = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-reviewed_at"]

    def __str__(self) -> str:
        return f"Review #{self.pk} ({self.get_decision_display()})"

    def save(self, *args, **kwargs):
        """Append-only: a written review can never be rewritten (Phase 5).

        This is the one piece of Phase 5 logic that lives in a model method
        rather than a service, and deliberately so. The rule is "this row
        cannot change", which is a property of the row itself — putting it in a
        service would leave the admin, the shell, and any future API able to
        mutate a decision that someone is entitled to rely on. The service
        layer still owns everything about what a review *means*.

        Two known gaps, stated rather than papered over:

        * ``ReviewRecord.objects.filter(...).update(...)`` does not call
          ``save()`` and is therefore not blocked. Django offers no hook for
          it; only a database trigger would close it, which ADR-003 §2 declines
          for the same reasons it declines a transition trigger. A test pins
          this so nobody assumes protection that is not there.
        * Deletion is unaffected. Immutability here means *not editable*;
          wiping a development database is an administrative act outside that
          guarantee, and ``core/dev_data.py`` depends on it.
        """
        if self.pk is not None:
            from .review_errors import ImmutableReviewError

            raise ImmutableReviewError(
                f"ReviewRecord #{self.pk} is immutable: a recorded review "
                f"decision cannot be edited. Record a new review instead."
            )
        return super().save(*args, **kwargs)


class SchedulerDecision(models.Model):
    """Audit record for one auto-fill scheduler tick (Phase 4).

    Every run writes one of these, including dry runs and runs that assigned
    nothing. That is deliberate: "the scheduler considered this and chose
    nobody" is exactly the question asked when work sits unassigned, and it is
    unanswerable from the assignments table alone.

    ``tick_key`` is the idempotency handle. A replayed tick finds its own
    previous row and returns that result rather than assigning a second time,
    so a retried cron invocation or a re-run after a timeout cannot double-fill.
    """

    class Mode(models.TextChoices):
        PUSH = "push", "Push (applied)"
        DRY_RUN = "dry_run", "Dry run (proposed only)"

    tick_key = models.CharField(
        max_length=128,
        unique=True,
        help_text="Idempotency handle; a replay returns this row's result.",
    )
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.PUSH)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduler_decisions",
        help_text="Manager who triggered it; null for an automated tick.",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scheduler_decisions",
        help_text="Set when the run was scoped to one project.",
    )

    candidates_considered = models.PositiveIntegerField(default=0)
    users_available = models.PositiveIntegerField(default=0)
    assignments_made = models.PositiveIntegerField(default=0)
    # Per-assignment detail: task, user, score, and the score's components, so a
    # decision can be explained after the fact rather than merely reported.
    decisions = models.JSONField(default=list, blank=True)
    weights = models.JSONField(default=dict, blank=True)
    duration_ms = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["-created_at"], name="idx_sched_decision_recent"),
        ]

    def __str__(self) -> str:
        return (
            f"Scheduler {self.mode} {self.created_at:%Y-%m-%d %H:%M} "
            f"-> {self.assignments_made} assignment(s)"
        )


# --- Phase 7: annotation operation model ------------------------------------
# Append-only op log + work sessions. See adr/ADR-005-annotation-operation-model.md.
#
# Operations hang directly off the canonical single-assignee AnnotationTask.


class WorkSession(models.Model):
    """One editing session — when someone was actually working, and for how long.

    Doc 16 models ``AnnotationSession (op log, timing)`` as a single node: the
    session that carries operations is the same object that carries timing.

    ``active_seconds`` is accumulated **from the server clock only**. Client
    timestamps are stored for diagnostics and never trusted for duration —
    otherwise a wrong or hostile clock could invent work. See ADR-005 §7.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        AnnotationTask, on_delete=models.CASCADE, related_name="work_sessions"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="work_sessions",
    )

    started_at = models.DateTimeField(auto_now_add=True)
    # The last heartbeat the server accepted. Also the point from which the next
    # interval is measured, and what the staleness sweep compares against.
    last_heartbeat_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    # Sum of capped, non-idle intervals. Never negative by construction.
    active_seconds = models.PositiveIntegerField(default=0)
    # How many heartbeats were credited vs received — a large gap means a client
    # is heartbeating far more often than it is working.
    heartbeats = models.PositiveIntegerField(default=0)

    # --- Time-tracking bookkeeping -----------------------------------------
    # A stable per-tab token supplied by the client. Its only job is
    # idempotency: a retried or duplicated start must resume the tab's existing
    # session instead of opening a second one. It is not a credential — the
    # actor is taken from the authenticated request, never from here.
    client_token = models.CharField(max_length=64, blank=True, db_index=True)
    # Why the session stopped, for reconciliation and support questions.
    close_reason = models.CharField(max_length=32, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(active_seconds__gte=0),
                name="work_session_active_seconds_non_negative",
            ),
        ]
        indexes = [
            # "sessions for this person on this task", the aggregation path.
            models.Index(fields=["actor", "task"], name="idx_session_actor_task"),
            # The staleness sweep: open sessions, oldest heartbeat first.
            models.Index(
                fields=["ended_at", "last_heartbeat_at"], name="idx_session_stale"
            ),
            # Idempotent start: "does this tab already have an open session?"
            models.Index(
                fields=["actor", "task", "client_token"],
                name="idx_session_resume",
            ),
        ]

    def __str__(self) -> str:
        who = self.actor.get_username() if self.actor_id else "?"
        return f"Session {self.id} ({who}, task #{self.task_id}, {self.active_seconds}s)"

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


class WorkInterval(models.Model):
    """One contiguous stretch of measured work. The unit reporting sums.

    ``WorkSession.active_seconds`` is a per-session counter, and it is correct
    for what it measures — but it cannot answer the two questions reporting
    actually asks:

    * **Two tabs are not two people.** Summing per-session counters doubles an
      hour worked with two tabs open. The answer is the *union* of the spans,
      which needs the spans.
    * **A gap inside a session is not work.** Unioning whole sessions
      (``started_at`` → ``last_heartbeat_at``) would swallow the idle gaps that
      :func:`annotation.sessions.credited_seconds` correctly refused to credit,
      so a laptop closed at lunch and reopened at three would bill three hours.

    An interval is opened when timing starts, extended by each heartbeat that
    credits time, and closed when the heartbeat gap exceeds the cap or the idle
    timeout — which is exactly where the crediting policy says one stretch of
    work ended and another began. Reporting takes the union of intervals per
    annotator, so overlap counts once and gaps count not at all.

    Append-only in spirit: rows are created and then closed, never rewritten to
    mean a different stretch of time, so the table stays an audit trail.
    """

    class CloseReason(models.TextChoices):
        ENDED = "ended", "Client ended the session"
        IDLE = "idle", "Idle gap exceeded the timeout"
        CAPPED = "capped", "Heartbeat gap exceeded the per-interval cap"
        SUBMITTED = "submitted", "Task was submitted"
        EXPIRED = "expired", "Abandoned — capped at the last heartbeat"
        SUPERSEDED = "superseded", "Assignment or permission changed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        WorkSession, on_delete=models.CASCADE, related_name="intervals"
    )
    # task/volume/actor are denormalised from ``session`` so every aggregation
    # the People drill-down needs is one indexed query rather than a join chain
    # per row. They are written once at creation and never diverge: a session's
    # task and actor are immutable.
    task = models.ForeignKey(
        AnnotationTask, on_delete=models.CASCADE, related_name="work_intervals"
    )
    volume = models.ForeignKey(
        "volumes.Volume", on_delete=models.CASCADE, related_name="work_intervals"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="work_intervals",
    )

    # Both from the server clock, always. A client cannot propose either.
    started_at = models.DateTimeField(db_index=True)
    # Null while the interval is still open. Readers must not treat an open
    # interval as running to "now" — see
    # ``annotation.timing.effective_interval_end``.
    ended_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(
        max_length=32, choices=CloseReason.choices, blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["started_at", "id"]
        indexes = [
            # The People drill-down: this annotator's work, newest span first.
            models.Index(fields=["actor", "started_at"], name="idx_interval_actor"),
            # Volume and dataset roll-ups.
            models.Index(fields=["volume", "actor"], name="idx_interval_volume"),
            models.Index(fields=["task", "actor"], name="idx_interval_task"),
            # Reconciliation: which intervals are still open?
            models.Index(fields=["ended_at"], name="idx_interval_open"),
        ]

    def __str__(self) -> str:
        end = self.ended_at.isoformat() if self.ended_at else "open"
        return f"Interval {self.id} ({self.started_at.isoformat()} → {end})"

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


class AnnotationOperation(models.Model):
    """One recorded edit. Append-only; never updated, never deleted.

    Doc 22's ``OperationLog[] (id, prev_id, type, payload, client_ts,
    server_ts, user)``, with the sequence and version machinery ADR-005 §3
    specifies.

    **The payload is metadata, not voxels.** The working memmap TIFF remains the
    materialized state and the authoritative source for reads; this log is
    history and undo substrate. Anything larger than
    ``MITO_OP_PAYLOAD_MAX_BYTES`` must be referenced through ``payload_ref``
    rather than embedded, so the log stays scannable without touching image
    data. See ADR-005 §2 conflict B and §5.
    """

    class Kind(models.TextChoices):
        PAINT_SLICE = "paint_slice", "Paint slice"
        ERASE_SLICE = "erase_slice", "Erase slice"
        TRACK_SLICES = "track_slices", "Track across slices"
        PREDICT_COMMIT = "predict_commit", "Commit AI prediction"
        MERGE_LABELS = "merge_labels", "Merge labels"
        SPLIT_COMPONENTS = "split_components", "Split components"
        WATERSHED = "watershed", "Watershed"
        UNDO = "undo", "Undo"
        REDO = "redo", "Redo"

    # Client-generatable, so an operation is identifiable before the server
    # sees it — which is what makes a crashed-before-response retry resolvable.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        AnnotationTask, on_delete=models.CASCADE, related_name="operations"
    )
    session = models.ForeignKey(
        WorkSession,
        on_delete=models.SET_NULL,
        null=True, blank=True, related_name="operations",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True, related_name="annotation_operations",
    )

    # Dense, monotonic, per task. Allocated under a lock on the task row; the
    # unique constraint below makes a gap or duplicate impossible even if the
    # service layer is bypassed.
    seq = models.PositiveIntegerField()
    kind = models.CharField(max_length=32, choices=Kind.choices)
    schema_version = models.PositiveSmallIntegerField(default=1)

    payload = models.JSONField(default=dict, blank=True)
    # Where the voxel bytes live, when an operation has any. Relative to
    # MITO_DATA_ROOT, exactly like every other artifact path in this codebase.
    payload_ref = models.CharField(max_length=512, blank=True, default="")
    # sha256 of the canonical payload. Lets corruption be detected without
    # replaying anything.
    payload_digest = models.CharField(max_length=64, blank=True, default="")

    # Server clock is authoritative; client_ts is diagnostic only.
    server_ts = models.DateTimeField(auto_now_add=True, db_index=True)
    client_ts = models.DateTimeField(null=True, blank=True)

    # Retry handle. Unique per (task, actor, key) — see the constraint below.
    idempotency_key = models.CharField(max_length=128, blank=True, default="")

    # Undo/redo: the operation this one reverses, and when this one was itself
    # reversed. Nothing is ever deleted to implement undo.
    inverse_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="inverted_by",
    )
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["task_id", "seq"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "seq"], name="uniq_operation_seq_per_task"
            ),
            # Partial: the common case supplies no key and must stay
            # unconstrained.
            models.UniqueConstraint(
                fields=["task", "actor", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_operation_idempotency_key",
            ),
        ]
        indexes = [
            models.Index(fields=["task", "-seq"], name="idx_operation_recent"),
            models.Index(fields=["actor", "-server_ts"], name="idx_operation_actor"),
        ]

    def __str__(self) -> str:
        return f"Op #{self.seq} {self.kind} on task #{self.task_id}"

    @property
    def is_undone(self) -> bool:
        return self.undone_at is not None

    def save(self, *args, **kwargs):
        """Append-only: a recorded operation is never rewritten.

        Same mechanism and the same reasoning as ``ReviewRecord`` in Phase 5 —
        the rule is a property of the row, so a service-layer check would leave
        the shell and the admin able to rewrite history someone relies on.

        ``undone_at`` is the one field that must change after insert, so it is
        applied through an explicit, narrow update path
        (``operations._mark_undone``) rather than by relaxing this guard. The
        known gap is the same as Phase 5's: ``queryset.update()`` bypasses
        ``save()`` entirely, which is what that path uses and what a test pins.
        """
        if self.pk is not None and not self._state.adding:
            from .review_errors import ImmutableReviewError

            raise ImmutableReviewError(
                f"AnnotationOperation {self.pk} is immutable: the operation log "
                f"is append-only. Record a new operation instead."
            )
        return super().save(*args, **kwargs)
