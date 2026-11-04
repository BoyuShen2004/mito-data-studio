"""Stitching phase 5: join labels into objects from reviewed decisions.

Reads candidates.csv and id_map.csv from the work directory and one or more
decisions.csv files from stitch_review. Union-find over global IDs merges only
"yes" pairs; each object's id is its smallest global id. Refuses decisions
that match no candidate and conflicting decisions for the same pair. Writes
objects.csv and objects_summary.csv. See docs/engineering/stitching.md.

    python manage.py stitch_objects --work stitch_p10 --decisions decisions.csv
    python manage.py stitch_objects --work stitch_p10 --decisions a.csv --decisions b.csv --require-complete
"""

from django.core.management.base import BaseCommand, CommandError

from annotation.stitching import StitchingError, run_objects


class Command(BaseCommand):
    help = "Stitching phase 5: write objects.csv and objects_summary.csv from review decisions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--work", required=True,
            help="Work directory written by the earlier stitching phases.",
        )
        parser.add_argument(
            "--decisions", action="append", required=True,
            help="A decisions.csv exported by the review page; repeat for several files.",
        )
        parser.add_argument(
            "--require-complete", action="store_true",
            help="Refuse if any candidate has no decision.",
        )

    def handle(self, *args, **opts):
        try:
            result = run_objects(opts["work"], opts["decisions"], require_complete=opts["require_complete"])
        except StitchingError as exc:
            raise CommandError(str(exc))

        c = result.counts
        self.stderr.write(
            f"decisions: yes {c['yes']}, no {c['no']}, unsure {c['unsure']}, undecided {c['undecided']}"
        )
        self.stderr.write(f"{c['objects']} object(s), {c['merged_objects']} built from more than one label")
        if result.repeated_tile_objects:
            self.stderr.write(self.style.WARNING(
                f"{len(result.repeated_tile_objects)} object(s) hold two labels from the same tile; "
                f"check them: {', '.join(map(str, result.repeated_tile_objects[:20]))}"
            ))
        self.stderr.write(self.style.SUCCESS("ok"))
