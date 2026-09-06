"""Delete read notifications past their useful life.

The inbox is append-only in normal operation, so without this the table only
grows: one active project produces a notification per assignment, submission,
review decision, and hard-case reply, for every person who should see it. That
is fine for a year and not fine for five.

Only **read** notifications are removed, and only past the retention window.
An unread row is still owed to somebody however old it is — deleting it would
silently drop the one thing the inbox exists to guarantee.

    # crontab / systemd timer, weekly
    python manage.py prune_notifications --days 90
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Notification

DEFAULT_RETENTION_DAYS = 90


class Command(BaseCommand):
    help = "Delete read notifications older than the retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=DEFAULT_RETENTION_DAYS,
            help=f"Retention window in days (default {DEFAULT_RETENTION_DAYS}).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report the count without deleting anything.",
        )

    def handle(self, *args, **options):
        days = int(options["days"])
        if days < 1:
            # A zero-day window would delete a notification the moment it was
            # read, which is indistinguishable from losing it.
            self.stderr.write("--days must be at least 1.")
            return
        cutoff = timezone.now() - timedelta(days=days)

        stale = Notification.objects.filter(
            read_at__isnull=False, read_at__lt=cutoff
        )
        count = stale.count()
        if options["dry_run"]:
            self.stdout.write(
                f"Would delete {count} read notification(s) read before "
                f"{cutoff.date().isoformat()}."
            )
            return

        deleted, _ = stale.delete()
        remaining_unread = Notification.objects.filter(read_at__isnull=True).count()
        self.stdout.write(
            f"Deleted {deleted} read notification(s). "
            f"{remaining_unread} unread notification(s) retained."
        )
