from django.core.management.base import BaseCommand

from volumes.models import Volume
from volumes.region_masks import refresh_region_mask_coverage


class Command(BaseCommand):
    help = "Compute cached non-zero region-mask coverage for registered volumes."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        queryset = Volume.objects.exclude(region_mask_path="", region_mask_file="")
        if not options["force"]:
            queryset = queryset.filter(region_mask_coverage__isnull=True)
        updated = failed = 0
        for volume in queryset.iterator():
            try:
                refresh_region_mask_coverage(volume)
                updated += 1
            except Exception as exc:
                failed += 1
                self.stderr.write(f"Volume {volume.pk}: {exc}")
        self.stdout.write(
            self.style.SUCCESS(f"Coverage updated: {updated}; failed: {failed}")
        )
