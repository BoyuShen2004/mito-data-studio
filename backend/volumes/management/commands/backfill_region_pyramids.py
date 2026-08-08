"""Queue ROI pyramid builds for volumes that came online without one.

Registration enqueues an image build and a region build together, so anything
registered since the ROI layer shipped needs nothing from this command. Volumes
registered *before* it only ever got the image job, and re-registering data that
is already online and assigned is not an acceptable way to earn the second one.

    python manage.py backfill_region_pyramids --dry-run
    python manage.py backfill_region_pyramids --project 3
    python manage.py backfill_region_pyramids --limit 10

The command only queues; the dispatcher
(``manage.py run_processing_dispatcher``) still decides how many run at once.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from volumes.models import Volume
from volumes.services import backfill_region_pyramids


class Command(BaseCommand):
    help = (
        "Queue a region-mask pyramid build for every volume that has a region "
        "mask but does not stream it yet."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--project",
            action="append",
            dest="projects",
            default=[],
            help="Restrict to a project id or title (repeatable).",
        )
        parser.add_argument(
            "--volume",
            action="append",
            dest="volumes",
            default=[],
            type=int,
            help="Restrict to a volume id (repeatable).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Queue at most this many builds this run (default: no limit).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be queued and change nothing.",
        )

    def handle(self, *args, **options):
        queryset = Volume.objects.all()
        if options["projects"]:
            selector = Q()
            for value in options["projects"]:
                selector |= (
                    Q(project_id=int(value))
                    if str(value).isdigit()
                    else Q(project__title=value)
                )
            queryset = queryset.filter(selector)
        if options["volumes"]:
            queryset = queryset.filter(pk__in=options["volumes"])
        if (options["projects"] or options["volumes"]) and not queryset.exists():
            raise CommandError("No volumes matched that selection.")

        report = backfill_region_pyramids(
            queryset=queryset,
            limit=options["limit"],
            dry_run=options["dry_run"],
        )

        prefix = "Would queue" if options["dry_run"] else "Queued"
        self.stdout.write(
            f"Eligible (region mask, not streaming): {len(report['eligible'])}"
        )
        self.stdout.write(f"{prefix}: {len(report['queued'])} {report['queued']}")
        if report["in_flight"]:
            self.stdout.write(
                f"Already building: {len(report['in_flight'])} {report['in_flight']}"
            )
        if report["skipped"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Not queued (limit reached or submission refused): "
                    f"{len(report['skipped'])} {report['skipped']}"
                )
            )
        self.stdout.write(self.style.SUCCESS("Backfill pass complete."))
