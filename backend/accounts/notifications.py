"""Per-person inbox writes.

One entry point, :func:`notify`, so every notification is written the same
way — mirroring :mod:`accounts.audit`, and best-effort for the same reason: a
failed inbox write must never take down the assignment or review that
triggered it. A missing notification is bad; a 500 on a successful approval is
worse.

Notifications are written **regardless of** ``settings.FEATURE_NOTIFICATIONS``.
The flag gates the *API*, not the recording, so a deployment that enables the
inbox later shows the history that accumulated rather than starting empty.

Deliberately separate from :mod:`accounts.audit`. Audit records every
permission-relevant action forever and is never mutated; this records the small
fraction worth interrupting somebody about, and is mutated once when read.
"""

from __future__ import annotations

import logging

from django.db import models, transaction
from django.utils import timezone

from core.choices import NotificationVerb

from .models import Notification

logger = logging.getLogger(__name__)


def notify(
    recipient,
    verb,
    *,
    title: str,
    body: str = "",
    url: str = "",
    actor=None,
    target=None,
) -> Notification | None:
    """Write one notification. Returns the row, or ``None`` if it failed.

    Never notifies somebody about their own action: an annotator who submits
    does not need telling that they submitted. Callers therefore do not have to
    filter the actor out themselves at every call site.
    """
    if recipient is None or not getattr(recipient, "pk", None):
        return None
    if actor is not None and getattr(actor, "pk", None) == recipient.pk:
        return None

    target_type = ""
    target_id = ""
    if target is not None:
        target_type = target.__class__.__name__
        target_id = str(getattr(target, "pk", "") or "")

    try:
        return Notification.objects.create(
            recipient=recipient,
            verb=str(verb),
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            target_type=target_type,
            target_id=target_id,
            title=title[:200],
            body=body[:500],
            url=url[:300],
        )
    except Exception:  # noqa: BLE001 - never let an inbox write break the action
        logger.exception(
            "Failed to write notification %s to %s", verb, getattr(recipient, "pk", "?")
        )
        return None


def notify_many(entries) -> int:
    """Write many notifications in one statement. Returns how many landed.

    ``entries`` is an iterable of dicts shaped like :func:`notify`'s keyword
    arguments plus ``recipient`` and ``verb``.

    Exists for the same reason :func:`accounts.audit.record_audit_bulk` does:
    withdrawing a project's assignments notifies every affected annotator at
    once, and one INSERT per person would make the notification the only
    per-item cost in an otherwise constant-cost operation.
    """
    rows = []
    for entry in entries:
        recipient = entry.get("recipient")
        if recipient is None or not getattr(recipient, "pk", None):
            continue
        actor = entry.get("actor")
        if actor is not None and getattr(actor, "pk", None) == recipient.pk:
            continue
        target = entry.get("target")
        rows.append(
            Notification(
                recipient=recipient,
                verb=str(entry["verb"]),
                actor=actor if getattr(actor, "is_authenticated", False) else None,
                target_type=target.__class__.__name__ if target is not None else "",
                target_id=(
                    str(getattr(target, "pk", "") or "") if target is not None else ""
                ),
                title=str(entry.get("title", ""))[:200],
                body=str(entry.get("body", ""))[:500],
                url=str(entry.get("url", ""))[:300],
            )
        )
    if not rows:
        return 0
    try:
        Notification.objects.bulk_create(rows)
        return len(rows)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to write %d notifications in bulk", len(rows))
        return 0


def unread_count(user) -> int:
    """How many unread notifications this person has.

    One indexed count on ``(recipient, read_at, -created_at)``. The frontend
    bell polls this, so it must never grow a join.
    """
    if user is None or not getattr(user, "pk", None):
        return 0
    return Notification.objects.filter(recipient=user, read_at__isnull=True).count()


def inbox(user, *, unread_only: bool = False, limit: int = 50, offset: int = 0):
    """This person's notifications, newest first."""
    queryset = Notification.objects.filter(recipient=user).select_related("actor")
    if unread_only:
        queryset = queryset.filter(read_at__isnull=True)
    return queryset[offset : offset + limit]


@transaction.atomic
def mark_read(user, ids=None) -> int:
    """Mark this person's notifications read. Returns how many changed.

    ``ids=None`` marks everything unread read. Scoped to ``recipient=user`` in
    the same statement rather than checked first, so no caller can mark
    somebody else's inbox read by passing their ids.

    Already-read rows are excluded from the filter so ``read_at`` keeps the
    time of *first* reading rather than being pushed forward by a re-read.
    """
    queryset = Notification.objects.filter(recipient=user, read_at__isnull=True)
    if ids is not None:
        ids = [int(value) for value in ids]
        if not ids:
            return 0
        queryset = queryset.filter(id__in=ids)
    return queryset.update(read_at=timezone.now())


# --- Message builders -------------------------------------------------------
# Kept here rather than inline at each call site so the wording of a given
# event is defined once, and so the SPA deep links stay in one place when the
# routes change.


def notify_task_assigned(task, *, actor=None) -> Notification | None:
    if task.assigned_to is None:
        return None
    return notify(
        task.assigned_to,
        NotificationVerb.TASK_ASSIGNED,
        title=f"{task.volume.name} assigned to you",
        body=f"{task.project.title} · {task.frame_label}",
        url=f"/tasks/{task.pk}",
        actor=actor,
        target=task,
    )


def notify_submission_received(submission, managers, *, actor=None) -> int:
    """Tell the project's managers that something is waiting for review."""
    task = submission.task
    who = submission.annotator.get_username() if submission.annotator else "someone"
    return notify_many(
        {
            "recipient": manager,
            "verb": NotificationVerb.SUBMISSION_RECEIVED,
            "title": f"{who} submitted {task.volume.name}",
            "body": f"{task.project.title} · {task.frame_label}",
            "url": f"/submissions/{submission.pk}/review",
            "actor": actor,
            "target": submission,
        }
        for manager in managers
    )


def notify_submission_reviewed(submission, decision, *, actor=None, comments=""):
    task = submission.task
    return notify(
        submission.annotator,
        NotificationVerb.SUBMISSION_REVIEWED,
        title=f"{task.volume.name}: {str(decision).replace('_', ' ')}",
        body=comments or f"{task.project.title} · {task.frame_label}",
        url=f"/tasks/{task.pk}",
        actor=actor,
        target=submission,
    )


def notify_quality_flagged(submission, managers, score, *, threshold) -> int:
    task = submission.task
    shown = "—" if score is None else f"{score:.2f}"
    return notify_many(
        {
            "recipient": manager,
            "verb": NotificationVerb.QUALITY_FLAGGED,
            "title": f"Low agreement on {task.volume.name}",
            "body": f"Dice {shown} is below the {threshold:.2f} threshold.",
            "url": f"/submissions/{submission.pk}/review",
            "target": submission,
        }
        for manager in managers
    )


def manager_recipients():
    """Every user who should see a manager-facing notification.

    Managers are an **org-wide** role in this application, not a per-project
    one (see ``accounts.roles.is_manager`` and
    ``annotation.services.is_project_member``, which returns True for any
    manager on any project). Review queues are therefore shared, and so is the
    notification for them. If per-project managers are introduced later, this
    is the one function that has to learn about it.
    """
    from django.contrib.auth import get_user_model
    from core.choices import UserRole

    User = get_user_model()
    return User.objects.filter(
        models.Q(is_superuser=True) | models.Q(profile__role=UserRole.MANAGER),
        is_active=True,
    ).distinct()


def project_audience(project, *, exclude=None):
    """Everyone who should hear about something happening in ``project``.

    Mirrors ``annotation.services.is_project_member`` — the manager(s), the
    requester who owns it, explicit members, every annotator holding a task on
    it, and (behind ``FEATURE_TEAMS``) the granted teams. Kept as one query set
    rather than a predicate loop so a busy project does not turn one hard-case
    reply into a per-member round trip.

    If the two ever disagree, this one is wrong: membership is decided by
    ``is_project_member``, and this exists only to enumerate it.
    """
    from django.contrib.auth import get_user_model
    from django.conf import settings
    from core.choices import UserRole

    User = get_user_model()
    if project is None:
        return User.objects.none()

    criteria = (
        models.Q(is_superuser=True)
        | models.Q(profile__role=UserRole.MANAGER)
        | models.Q(created_projects=project)
        | models.Q(project_memberships__project=project)
        | models.Q(annotation_tasks__project=project)
    )
    if getattr(settings, "FEATURE_TEAMS", False):
        criteria |= models.Q(team_memberships__team__projects=project)

    audience = User.objects.filter(criteria, is_active=True).distinct()
    if exclude is not None and getattr(exclude, "pk", None):
        audience = audience.exclude(pk=exclude.pk)
    return audience


def notify_task_withdrawn(withdrawn_pairs, *, actor=None) -> int:
    """Tell each former assignee that work was taken back.

    Takes ``(task, former assignee)`` pairs, not tasks: by the time a caller
    can report a withdrawal the task's ``assigned_to`` is already cleared, so
    the recipient has to be carried alongside it.

    One notification per task rather than one per person: an annotator who
    loses three volumes needs to know which three, and collapsing that into
    "3 tasks withdrawn" hides the one they were in the middle of.
    """
    return notify_many(
        {
            "recipient": annotator,
            "verb": NotificationVerb.TASK_WITHDRAWN,
            "title": f"{task.volume.name} was withdrawn",
            "body": f"{task.project.title} · {task.frame_label}",
            "url": f"/projects/{task.project_id}",
            "actor": actor,
            "target": task,
        }
        for task, annotator in withdrawn_pairs
        if annotator is not None
    )


def notify_hard_case_opened(case, *, actor=None) -> int:
    volume = case.volume.name if case.volume_id else "a volume"
    return notify_many(
        {
            "recipient": person,
            "verb": NotificationVerb.HARD_CASE_OPENED,
            "title": f"Hard case on {volume}",
            "body": (case.note or f"Instance #{case.label_id}")[:500],
            "url": f"/hard-cases/{case.pk}",
            "actor": actor,
            "target": case,
        }
        for person in project_audience(case.project, exclude=actor)
    )


def notify_hard_case_replied(message, *, actor=None) -> int:
    """Notify the case's audience — minus whoever just wrote the reply."""
    case = message.hard_case
    volume = case.volume.name if case.volume_id else "a volume"
    who = actor.get_username() if actor is not None else "Someone"
    return notify_many(
        {
            "recipient": person,
            "verb": NotificationVerb.HARD_CASE_REPLIED,
            "title": f"{who} replied on {volume}",
            "body": message.body[:500],
            "url": f"/hard-cases/{case.pk}",
            "actor": actor,
            "target": case,
        }
        for person in project_audience(case.project, exclude=actor)
    )


def notify_deadline_approaching(task, *, days_left: int) -> Notification | None:
    if task.assigned_to is None:
        return None
    when = "today" if days_left == 0 else f"in {days_left} day(s)"
    return notify(
        task.assigned_to,
        NotificationVerb.DEADLINE_APPROACHING,
        title=f"{task.volume.name} is due {when}",
        body=f"{task.project.title} · {task.frame_label}",
        url=f"/tasks/{task.pk}",
        target=task,
    )


def notify_milestone_at_risk(milestone, recipients, *, remaining: int) -> int:
    return notify_many(
        {
            "recipient": person,
            "verb": NotificationVerb.MILESTONE_AT_RISK,
            "title": f"Milestone “{milestone.name}” is at risk",
            "body": (
                f"{remaining} left, due {milestone.due_on.isoformat()}."
            ),
            "url": f"/projects/{milestone.project_id}?tab=delivery",
            "target": milestone,
        }
        for person in recipients
    )
