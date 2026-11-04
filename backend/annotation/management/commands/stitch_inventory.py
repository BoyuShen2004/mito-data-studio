"""Stitching phase 1: inventory label tiles and the seams between them.

Offline and read-only for the tiles. Parses tile names (z0-z1_y0-y1_x0-x1...)
into boxes, finds every shared face, and reports overlaps, gaps, duplicates,
missing grid positions and shape mismatches. See docs/engineering/stitching.md.

    python manage.py stitch_inventory --tiles /data/nag_p10/labels --work stitch_p10
    python manage.py stitch_inventory --tiles tiles.txt --work stitch_p10
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from annotation.stitching import StitchingError, run_inventory


class Command(BaseCommand):
    help = "Stitching phase 1: write tiles.csv, seams.csv and issues.csv for a set of label tiles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tiles", required=True,
            help="Directory of label tiles, or a text file with one tile name or path per line.",
        )
        parser.add_argument(
            "--work", required=True,
            help="Work directory for the stitching CSVs (created if missing).",
        )

    def handle(self, *args, **opts):
        try:
            inventory = run_inventory(opts["tiles"], opts["work"])
        except StitchingError as exc:
            raise CommandError(str(exc))

        self.stderr.write(
            f"{len(inventory.tiles)} tile(s), {len(inventory.seams)} seam(s) -> {opts['work']}"
        )
        if inventory.issues:
            counts = Counter(issue.kind for issue in inventory.issues)
            summary = ", ".join(f"{kind}: {n}" for kind, n in sorted(counts.items()))
            self.stderr.write(self.style.WARNING(f"issues ({summary}); see issues.csv"))
        else:
            self.stderr.write(self.style.SUCCESS("ok, no issues"))
