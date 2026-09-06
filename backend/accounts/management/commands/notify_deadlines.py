"""Time-based notifications: approaching task deadlines and at-risk milestones.

Everything else in the inbox is emitted by the service function that caused it.
These two have no causing action — nothing *happens* when a deadline gets
closer — so they need a clock, which means a scheduled command.

**Idempotent per (recipient, target, day).** A cron that runs hourly, or a
retry after a partial failure, must not turn one approaching deadline into
twenty inbox rows. The check is a query against what was already written today
rather than a marker column, so re-running is safe even after a crash midway.

    # crontab / systemd timer, once a day
    python manage.py notify_deadlines

``--days`` sets how far ahead to look (default 3). ``--dry-run`` reports what
it would write without writing it.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from accounts.models import Notification
from accounts.notifications import (
    notify_deadline_approaching,
    notify_milestone_at_risk,
    project_audience,
)
from core.choices import NotificationVerb, TaskStatus


def _already_sent_today(recipient_id, verb, target_type, target_id) -> bool:
    """Has this exact notification already gone out since local midnight?"""
    # Local midnight. `localtime()` is aware when USE_TZ is on and naive when
    # it is off, which is exactly what the comparison against `created_at`
    # needs in either configuration.
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return Notification.objects.filter(
        recipient_id=recipient_id,
        verb=verb,
        target_type=target_type,
        target_id=str(target_id),
        created_at__gte=start,
    ).exists()


class Command(BaseCommand):
    help = "Notify assignees of approaching deadlines and managers of at-risk milestones."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=3,
            help="How many days ahead counts as approaching (default 3).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be sent without writing anything.",
        )

    def handle(self, *args, **options):
        days = max(0, int(options["days"]))
        dry_run = bool(options["dry_run"])
        today = timezone.localdate()
        horizon = today + timedelta(days=days)

        deadlines = self._task_deadlines(today, horizon, dry_run)
        milestones = self._milestones(today, horizon, dry_run)

        prefix = "Would send" if dry_run else "Sent"
        self.stdout.write(
            f"{prefix} {deadlines} deadline and {milestones} milestone "
            f"notification(s) for deadlines through {horizon.isoformat()}."
        )

    def _task_deadlines(self, today, horizon, dry_run) -> int:
        from annotation.models import AnnotationTask

        # Approved work is not owed, whatever its deadline said. Everything
        # else with a date inside the window is fair game, including rejected
        # and revision-requested work, which is still outstanding.
        tasks = (
            AnnotationTask.objects.filter(
                ~Q(status=TaskStatus.APPROVED),
                assigned_to__isnull=False,
                deadline__isnull=False,
                deadline__lte=horizon,
            )
            .select_related("volume", "project", "assigned_to")
            .order_by("deadline", "id")
        )

        sent = 0
        for task in tasks:
            if _already_sent_today(
                task.assigned_to_id,
                NotificationVerb.DEADLINE_APPROACHING,
                "AnnotationTask",
                task.pk,
            ):
                continue
            days_left = (task.deadline - today).days
            if dry_run:
                sent += 1
                continue
            if notify_deadline_approaching(task, days_left=days_left) is not None:
                sent += 1
        return sent

    def _milestones(self, today, horizon, dry_run) -> int:
        from core.statistics import milestone_progress
        from projects.models import Milestone

        milestones = (
            Milestone.objects.filter(
                due_on__lte=horizon, completed_at__isnull=True
            )
            .select_related("project")
            .prefetch_related("volumes")
        )

        sent = 0
        for milestone in milestones:
            progress = milestone_progress(milestone)
            # Only at-risk ones: a milestone whose work is already done needs
            # no warning just because its date is close.
            if progress["remaining"] <= 0:
                continue
            recipients = [
                person
                for person in project_audience(milestone.project)
                if not _already_sent_today(
                    person.pk,
                    NotificationVerb.MILESTONE_AT_RISK,
                    "Milestone",
                    milestone.pk,
                )
            ]
            if not recipients:
                continue
            if dry_run:
                sent += len(recipients)
                continue
            sent += notify_milestone_at_risk(
                milestone, recipients, remaining=progress["remaining"]
            )
        return sent
