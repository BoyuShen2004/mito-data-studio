"""Stitching phase 3: list merge candidates across every seam.

Reads tiles.csv, seams.csv and tile_ids.csv from the work directory. For each
seam only the two facing planes are read; every pair of nonzero labels at the
same position becomes a candidate with its contact size and fractions.
Writes candidates.csv. See docs/engineering/stitching.md.

    python manage.py stitch_candidates --work stitch_p10
"""

from django.core.management.base import BaseCommand, CommandError

from annotation.stitching import StitchingError, run_candidates


class Command(BaseCommand):
    help = "Stitching phase 3: write candidates.csv of label pairs touching across seams."

    def add_arguments(self, parser):
        parser.add_argument(
            "--work", required=True,
            help="Work directory written by stitch_inventory and stitch_ids.",
        )

    def handle(self, *args, **opts):
        try:
            rows = run_candidates(opts["work"])
        except StitchingError as exc:
            raise CommandError(str(exc))

        seams = len({r["seam"] for r in rows})
        self.stderr.write(f"{len(rows)} candidate(s) on {seams} seam(s) with contact")
        self.stderr.write(self.style.SUCCESS("ok"))
