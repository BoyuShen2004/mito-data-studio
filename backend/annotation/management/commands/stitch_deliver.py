"""Stitching phase 7b: write the delivery folder for the scientist.

Runs the stitch_validate consistency checks, then writes mitochondria.csv (one
row per object reaching into the region, minus exclusions), summary.csv,
excluded.csv and a plain-language README.md. Needs region data and the
current stitch_measure outputs (bbox/centroid columns, measure_run.json). No
tile is read. See docs/engineering/stitching.md.

    python manage.py stitch_deliver --work stitch_p10
    python manage.py stitch_deliver --work stitch_p10 --exclude-global-ids not_mito.txt \
        --spot-check stitch_p10/decisions_auto_sample_50_seed1.csv --out stitch_p10/delivery
"""

from django.core.management.base import BaseCommand, CommandError

from annotation.stitching import StitchingError
from annotation.stitching_deliver import DELIVERY_FILES, run_deliver


class Command(BaseCommand):
    help = "Stitching phase 7b: write mitochondria.csv, summary.csv, excluded.csv and README.md."

    def add_arguments(self, parser):
        parser.add_argument(
            "--work", required=True,
            help="Work directory written by the earlier stitching phases (through stitch_measure).",
        )
        parser.add_argument(
            "--exclude-global-ids", default=None, metavar="FILE",
            help="Text file of global IDs (one per line) that reviewers judged not to be mitochondria; "
                 "their whole objects go to excluded.csv.",
        )
        parser.add_argument(
            "--out", default=None,
            help="Delivery folder (default <work>/delivery).",
        )
        parser.add_argument(
            "--overwrite", action="store_true",
            help=f"Replace {', '.join(DELIVERY_FILES)} in a non-empty delivery folder.",
        )
        parser.add_argument(
            "--auto-decisions", default=None, metavar="FILE",
            help="The auto-accepted decisions stitch_objects used (default <work>/decisions_auto.csv if present).",
        )
        parser.add_argument(
            "--spot-check", action="append", default=[], metavar="FILE",
            help="A reviewer's blind decisions.csv for a sample of auto-accepted pairs; repeatable. "
                 "The README reports how many agreed.",
        )

    def handle(self, *args, **opts):
        try:
            result = run_deliver(opts["work"], out=opts["out"], exclude_global_ids=opts["exclude_global_ids"],
                                 overwrite=opts["overwrite"], auto_decisions=opts["auto_decisions"],
                                 spot_checks=opts["spot_check"])
        except StitchingError as exc:
            raise CommandError(str(exc))

        metrics = {r["metric"]: r for r in result.summary}
        self.stderr.write(
            f"{metrics['objects']['all']} mitochondria ({metrics['objects']['core']} core), "
            f"{len(result.excluded)} excluded -> {result.out}"
        )
        for check in result.decisions.spot_checks:
            self.stderr.write(f"spot check {check['file']}: {check['agreed']} of {check['checked']} agreed")
        self.stderr.write(self.style.SUCCESS("ok"))
