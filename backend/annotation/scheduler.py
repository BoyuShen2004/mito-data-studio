"""Manager auto-fill scheduler for canonical single-assignee tasks.

The scheduler places work with available annotators who have idle capacity, and a
**dry run** that proposes assignments for a manager to review before applying.

Batched, not looped
-------------------
One tick issues a bounded number of statements no matter how many assignments
it makes:

    1. read available users + their current load          (2 queries)
    2. lock a batch of candidate tasks, SKIP LOCKED       (1 query)
    3. match in Python (pure, deterministic, no I/O)      (0 queries)
    4. update canonical task assignees                    (1 query)
    5. write the SchedulerDecision audit row              (1 query)

Correctness is not weakened by batching, because every guarantee is enforced by
the database rather than by this module:

===========================================  =================================
guarantee                                    enforced by
===========================================  =================================
one assignee per task                        AnnotationTask.assigned_to
no two schedulers taking the same task       FOR UPDATE ... SKIP LOCKED
idempotent replay of one tick                SchedulerDecision.tick_key unique
===========================================  =================================

A batch that would violate any of them fails at COMMIT rather than committing
corrupt state. That is what makes bulk creation safe here and would not make it
safe in a schema without those constraints.

Lock ordering
-------------
Only task rows are locked, always in one order (project priority, task
priority, created_at, id). No user row is ever locked. Two schedulers therefore
cannot deadlock by acquiring the same pair in opposite orders — they partition
the queue via SKIP LOCKED instead.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from accounts.audit import record_audit_bulk
from core.choices import ACTIVE_TASK_STATUSES, AuditVerb, TaskStatus

from .models import AnnotationTask, SchedulerDecision

logger = logging.getLogger(__name__)
User = get_user_model()


class SchedulerError(Exception):
    """A scheduler run that cannot proceed."""


def scheduler_enabled() -> bool:
    return bool(getattr(settings, "FEATURE_AUTO_FILL_SCHEDULER", False))


def _require_enabled() -> None:
    if not scheduler_enabled():
        raise SchedulerError(
            "Auto-fill scheduler is disabled (needs FEATURE_AUTO_FILL_SCHEDULER)."
        )


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


@dataclass
class AvailableUser:
    """An annotator who may receive pushed work, and how much they can take.

    ``idle_capacity`` is the vocabulary the roadmap requires — never "empty
    people" or similar.
    """

    user: object
    active_count: int
    max_active: int
    quality_score: float = 0.0
    assigned_this_tick: int = 0

    @property
    def idle_capacity(self) -> int:
        if self.max_active < 0:
            # Uncapped users are still bounded per tick, so one uncapped
            # annotator cannot absorb an entire batch.
            return max(
                int(getattr(settings, "MITO_SCHEDULER_MAX_BATCH", 200))
                - self.assigned_this_tick,
                0,
            )
        return max(self.max_active - self.active_count - self.assigned_this_tick, 0)

    @property
    def load_ratio(self) -> float:
        """0..1 — how full this annotator already is. Drives the load penalty."""
        if self.max_active <= 0:
            return 0.0
        return min((self.active_count + self.assigned_this_tick) / self.max_active, 1.0)


def available_users(*, now=None) -> list[AvailableUser]:
    """Annotators eligible for push assignment, with their idle capacity.

    A recency gate avoids pushing work to an account that has gone stale.

    Two queries: one for the profiles, one aggregate for current load.
    """
    from accounts.models import AnnotatorProfile

    now = now or timezone.now()
    profiles = list(
        AnnotatorProfile.objects.filter(
            is_active_annotator=True, user__is_active=True
        ).select_related("user")
    )
    if not profiles:
        return []

    active_days = int(getattr(settings, "MITO_SCHEDULER_ACTIVE_DAYS", 14))
    if active_days > 0:
        cutoff = now - timedelta(days=active_days)
        profiles = [
            p for p in profiles
            # A user who has never logged in is included: a freshly created
            # account should be reachable by the scheduler, not permanently
            # invisible because it has no last_login yet.
            if p.user.last_login is None or p.user.last_login >= cutoff
        ]
    if not profiles:
        return []

    load = dict(
        AnnotationTask.objects.filter(
            assigned_to__in=[p.user_id for p in profiles],
            status__in=ACTIVE_TASK_STATUSES,
        )
        .values_list("assigned_to")
        .annotate(n=Count("id"))
        .values_list("assigned_to", "n")
    )

    out = []
    for p in profiles:
        au = AvailableUser(
            user=p.user,
            active_count=load.get(p.user_id, 0),
            max_active=int(p.max_active_tasks),
            quality_score=float(p.quality_score or 0.0),
        )
        if au.idle_capacity > 0:
            out.append(au)
    # Deterministic order so a tie in score resolves the same way every run.
    out.sort(key=lambda a: (a.user.id,))
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

# Normalisation ceilings. Scores are only ever compared against each other, so
# these just keep every component on a common 0..1 scale.
_PRIORITY_SCALE = 10.0
_DEADLINE_HORIZON_DAYS = 14.0


def weights() -> dict:
    return dict(getattr(settings, "MITO_SCHEDULER_WEIGHTS", {}))


def score_components(task: AnnotationTask, au: AvailableUser, *, now=None) -> dict:
    """The normalised 0..1 inputs to the score, kept separate from the weights.

    Returned rather than folded away so ``SchedulerDecision`` can record *why*
    a task went to a particular annotator, which is the question asked when a
    manager disputes an assignment.
    """
    now = now or timezone.now()
    project_priority = getattr(task.project, "priority", 0) or 0
    deadline_urgency = 0.0
    if task.deadline is not None:
        days_left = (task.deadline - now.date()).days
        # Overdue saturates at 1.0 rather than going negative; nothing is more
        # urgent than "already late".
        deadline_urgency = min(
            max((_DEADLINE_HORIZON_DAYS - days_left) / _DEADLINE_HORIZON_DAYS, 0.0), 1.0
        )
    return {
        "project_priority": min(project_priority / _PRIORITY_SCALE, 1.0),
        "task_priority": min((task.priority or 0) / _PRIORITY_SCALE, 1.0),
        "deadline_urgency": deadline_urgency,
        "quality_history": min(max(au.quality_score, 0.0), 1.0),
        "current_load": au.load_ratio,
        # Rewards the least-loaded annotator, which is what stops one person
        # absorbing a batch while others sit idle.
        "fairness_bonus": 1.0 - au.load_ratio,
    }


def score(task: AnnotationTask, au: AvailableUser, *, now=None) -> tuple[float, dict]:
    comps = score_components(task, au, now=now)
    w = weights()
    total = (
        w.get("project_priority", 0.0) * comps["project_priority"]
        + w.get("task_priority", 0.0) * comps["task_priority"]
        + w.get("deadline_urgency", 0.0) * comps["deadline_urgency"]
        + w.get("quality_history", 0.0) * comps["quality_history"]
        + w.get("fairness_bonus", 0.0) * comps["fairness_bonus"]
        - w.get("current_load", 0.0) * comps["current_load"]
    )
    return round(total, 6), comps


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------


def _candidate_tasks(*, project=None, limit: int):
    """Lock a bounded batch of fillable tasks, skipping any another tick holds.

    This is the whole locking story. ``of=("self",)`` restricts the lock to the
    task rows — locking the joined project row as well would serialise every
    scheduler working on the same project for no benefit.
    """
    qs = (
        AnnotationTask.objects.filter(
            assigned_to__isnull=True,
            status=TaskStatus.UNASSIGNED,
        )
        .exclude(project__paused=True)
        .select_related("project")
        # Applied before slicing: Django cannot add a lock to an
        # already-sliced queryset.
        .select_for_update(skip_locked=True, of=("self",))
    )
    if project is not None:
        qs = qs.filter(project=project)
    qs = qs.order_by("-project__priority", "-priority", "created_at", "id")
    return list(qs[:limit])


def meets_requirements(user, task: AnnotationTask) -> bool:
    """Team grants are the sole assignment-eligibility policy.

    A project must explicitly grant a team and the annotator must currently
    belong to one of its granted teams. Experience-level policy belonged to the
    retired TaskType/claim surface and is intentionally not recreated here.
    """
    from accounts.teams import is_eligible_project_assignee

    return is_eligible_project_assignee(user, task.project)


@dataclass
class Proposal:
    task: AnnotationTask
    user: object
    score: float
    components: dict = field(default_factory=dict)

    def as_record(self) -> dict:
        return {
            "task_id": self.task.pk,
            "project_id": self.task.project_id,
            "user_id": self.user.id,
            "username": self.user.get_username(),
            "score": self.score,
            "components": self.components,
        }


def _match(tasks, users, *, now=None) -> list[Proposal]:
    """Pure, deterministic matching. No database access.

    Greedy by task order (already priority-sorted), and for each task the
    highest-scoring eligible annotator with capacity left. Greedy rather than
    globally optimal on purpose: an optimal assignment would need the whole
    bipartite graph, and the ordering guarantee managers actually asked for is
    "higher-priority work is placed first", which greedy gives exactly.
    """
    proposals: list[Proposal] = []
    for task in tasks:
        best: tuple[float, AvailableUser, dict] | None = None
        for au in users:
            if au.idle_capacity <= 0:
                continue
            if not meets_requirements(au.user, task):
                continue
            s, comps = score(task, au, now=now)
            # Ties break on user id, which _match's callers rely on for
            # reproducibility across runs.
            if best is None or s > best[0]:
                best = (s, au, comps)
        if best is None:
            continue
        s, au, comps = best
        proposals.append(Proposal(task=task, user=au.user, score=s, components=comps))
        au.assigned_this_tick += 1
    return proposals


# ---------------------------------------------------------------------------
# The tick
# ---------------------------------------------------------------------------


@dataclass
class SchedulerResult:
    tick_key: str
    mode: str
    proposals: list
    assignments_made: int
    candidates_considered: int
    users_available: int
    duration_ms: float
    replayed: bool = False
    skipped: bool = False
    decision_id: int | None = None


def run_auto_fill(
    *,
    project=None,
    dry_run: bool = False,
    limit: int | None = None,
    actor=None,
    tick_key: str | None = None,
    now=None,
) -> SchedulerResult:
    """One scheduler tick.

    ``dry_run=True`` computes and records the plan without creating anything —
    the hybrid mode the roadmap gates Phase 4 on ("dry-run demos").

    ``tick_key`` makes the tick idempotent. Replaying a key returns the recorded
    result of the original run instead of assigning again, so a retried cron
    invocation or a re-run after a client timeout cannot double-fill.
    """
    _require_enabled()
    started = time.perf_counter()
    now = now or timezone.now()
    tick_key = tick_key or f"auto-{uuid.uuid4()}"

    # Idempotency: check before doing any work.
    existing = SchedulerDecision.objects.filter(tick_key=tick_key).first()
    if existing is not None:
        return SchedulerResult(
            tick_key=tick_key,
            mode=existing.mode,
            proposals=existing.decisions,
            assignments_made=existing.assignments_made,
            candidates_considered=existing.candidates_considered,
            users_available=existing.users_available,
            duration_ms=existing.duration_ms,
            replayed=True,
            decision_id=existing.pk,
        )

    max_batch = int(getattr(settings, "MITO_SCHEDULER_MAX_BATCH", 200))
    users = available_users(now=now)
    total_idle = sum(u.idle_capacity for u in users)
    budget = min(limit or max_batch, max_batch, total_idle or 0)

    if not users or budget <= 0:
        return _record_empty(
            tick_key, dry_run, actor, project, len(users), started
        )

    mode = SchedulerDecision.Mode.DRY_RUN if dry_run else SchedulerDecision.Mode.PUSH

    with transaction.atomic():
        # Per-user capacity is not a database constraint. Two schedulers reading
        # capacity concurrently could each fill the same idle slots.
        #
        # A non-blocking advisory lock makes ticks mutually exclusive without
        # waiting. A tick that cannot get the lock does nothing and lets the next one run,
        # so there is no queue of blocked schedulers and no long-held lock.
        if not dry_run and not _acquire_scheduler_lock():
            return _record_skipped(tick_key, actor, project, len(users), started)

        tasks = _candidate_tasks(project=project, limit=budget)
        proposals = _match(tasks, users, now=now)

        if not dry_run and proposals:
            _apply(proposals, now=now)

        duration_ms = (time.perf_counter() - started) * 1000.0
        decision = SchedulerDecision.objects.create(
            tick_key=tick_key,
            mode=mode,
            actor=actor,
            project=project,
            candidates_considered=len(tasks),
            users_available=len(users),
            assignments_made=0 if dry_run else len(proposals),
            decisions=[p.as_record() for p in proposals],
            weights=weights(),
            duration_ms=duration_ms,
        )

    if not dry_run and proposals:
        # Outside the transaction: an audit failure must never roll back real
        # assignments. Bulk, because one INSERT per assignment was the only
        # per-item cost left in an otherwise constant-cost tick.
        record_audit_bulk(
            (
                actor,
                AuditVerb.TASK_ASSIGNED,
                p.task,
                {"assignee_id": p.user.id, "via": "auto_fill", "tick_key": tick_key},
            )
            for p in proposals
        )

    return SchedulerResult(
        tick_key=tick_key,
        mode=mode,
        proposals=[p.as_record() for p in proposals],
        assignments_made=0 if dry_run else len(proposals),
        candidates_considered=len(tasks),
        users_available=len(users),
        duration_ms=decision.duration_ms,
        decision_id=decision.pk,
    )


# Arbitrary but fixed key identifying "the auto-fill scheduler" to PostgreSQL's
# advisory lock space. Transaction-scoped, so it is released at COMMIT or
# ROLLBACK — a scheduler killed mid-batch cannot leave it held.
_SCHEDULER_LOCK_KEY = 0x4D49544F  # "MITO"


def _acquire_scheduler_lock() -> bool:
    """Try to become the only writing scheduler. Never blocks.

    Returns True on other database backends: advisory locks are a PostgreSQL
    feature, and the single-threaded development path on SQLite has no
    concurrent scheduler to exclude. Concurrency is asserted only on PostgreSQL
    for exactly this reason.
    """
    from django.db import connection as conn

    if conn.vendor != "postgresql":
        return True
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(%s)", [_SCHEDULER_LOCK_KEY])
        return bool(cur.fetchone()[0])


def _record_skipped(tick_key, actor, project, n_users, started):
    """Another scheduler holds the lock — record the no-op and stand down."""
    duration_ms = (time.perf_counter() - started) * 1000.0
    decision = SchedulerDecision.objects.create(
        tick_key=tick_key, mode=SchedulerDecision.Mode.PUSH, actor=actor,
        project=project, candidates_considered=0, users_available=n_users,
        assignments_made=0, decisions=[], weights=weights(),
        duration_ms=duration_ms,
    )
    logger.info("auto_fill tick %s skipped: another scheduler holds the lock",
                tick_key)
    return SchedulerResult(
        tick_key=tick_key, mode=SchedulerDecision.Mode.PUSH, proposals=[],
        assignments_made=0, candidates_considered=0, users_available=n_users,
        duration_ms=duration_ms, decision_id=decision.pk, skipped=True,
    )


def _record_empty(tick_key, dry_run, actor, project, n_users, started):
    """Nothing to do — still write the audit row.

    "The scheduler ran and placed nothing" is precisely the state someone
    investigates when work sits unassigned, and it is unanswerable from the
    assignments table alone.
    """
    mode = SchedulerDecision.Mode.DRY_RUN if dry_run else SchedulerDecision.Mode.PUSH
    duration_ms = (time.perf_counter() - started) * 1000.0
    decision = SchedulerDecision.objects.create(
        tick_key=tick_key, mode=mode, actor=actor, project=project,
        candidates_considered=0, users_available=n_users, assignments_made=0,
        decisions=[], weights=weights(), duration_ms=duration_ms,
    )
    return SchedulerResult(
        tick_key=tick_key, mode=mode, proposals=[], assignments_made=0,
        candidates_considered=0, users_available=n_users,
        duration_ms=duration_ms, decision_id=decision.pk,
    )


def _apply(proposals: list[Proposal], *, now) -> None:
    """Bulk-update the locked canonical task rows."""
    assigned = []
    for p in proposals:
        p.task.assigned_to = p.user
        p.task.status = TaskStatus.ASSIGNED
        p.task.assigned_at = now
        assigned.append(p.task)
    if assigned:
        AnnotationTask.objects.bulk_update(
            assigned, ["assigned_to", "status", "assigned_at"]
        )


# ---------------------------------------------------------------------------
# Manual override
# ---------------------------------------------------------------------------


def apply_plan(decision: SchedulerDecision, *, actor=None) -> SchedulerResult:
    """Apply a previously recorded dry run — the hybrid "manager approves" path.

    Re-validates every proposal against current state rather than trusting the
    stored plan: the world may have moved since the dry run, and a stale plan
    must not resurrect work someone has already taken.
    """
    _require_enabled()
    if decision.mode != SchedulerDecision.Mode.DRY_RUN:
        raise SchedulerError("Only a dry-run plan can be applied.")

    apply_key = f"{decision.tick_key}:applied"
    return run_auto_fill(
        project=decision.project,
        dry_run=False,
        limit=len(decision.decisions) or None,
        actor=actor,
        tick_key=apply_key,
    )
