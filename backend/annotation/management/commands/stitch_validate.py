"""Stitching phase 7a: validate the stitched objects.

Checks the work directory for consistency (voxel totals, yes-only
connectivity, nothing listed twice) and refuses on any violation. Then writes
validate_repeated_tile.csv (objects holding 2+ labels from one tile, classified
"touching" or "separate" from a read of only those labels' boxes) and
validate_largest.csv (the largest objects by member count and by cable length,
for visual checking). Needs label_boxes.csv and the bbox columns from the
current stitch_measure. See docs/engineering/stitching.md.

    python manage.py stitch_validate --work stitch_p10
    python manage.py stitch_validate --work stitch_p10 --top 50
"""

from django.core.management.base import BaseCommand, CommandError

from annotation.stitching import StitchingError
from annotation.stitching_validate import DEFAULT_TOP, run_validate


class Command(BaseCommand):
    help = "Stitching phase 7a: consistency checks, repeated-tile objects and the largest objects."

    def add_arguments(self, parser):
        parser.add_argument(
            "--work", required=True,
            help="Work directory written by the earlier stitching phases (through stitch_measure).",
        )
        parser.add_argument(
            "--top", type=int, default=DEFAULT_TOP,
            help=f"How many objects to list per ranking in validate_largest.csv (default {DEFAULT_TOP}).",
        )

    def handle(self, *args, **opts):
        def log(message):
            self.stderr.write(message)
            self.stderr.flush()

        try:
            result = run_validate(opts["work"], top=opts["top"], log=log)
        except StitchingError as exc:
            raise CommandError(str(exc))

        c = result.counts
        self.stderr.write(f"consistency: ok ({c['objects']} object(s), {c['measured_objects']} measured)")
        self.stderr.write(
            f"repeated-tile objects: {c['repeated_objects']} ({c['repeated_rows']} object/tile pair(s)): "
            f"{c['touching']} touching, {c['separate']} separate -> validate_repeated_tile.csv"
        )
        self.stderr.write(f"{len(result.largest)} row(s) -> validate_largest.csv")
        self.stderr.write(self.style.SUCCESS("ok"))
