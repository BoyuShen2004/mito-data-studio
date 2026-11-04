"""Stitching phase 4: build an offline review page for seam merge candidates.

Reads the work directory from phases 1-3 and pairs each prediction tile with
the raw image tile that has the same z0-z1_y0-y1_x0-x1 box. Writes a folder
(index.html plus PNG crops) that works opened from disk, with no server or
network, and exports decisions.csv for stitch_objects. Also writes
image_pairing.csv to the work directory. See docs/engineering/stitching.md.

    python manage.py stitch_review --work stitch_p10 --images /data/nag_p10/images
    python manage.py stitch_review --work stitch_p10 --images /data/nag_p10/images --shard 2/3
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from annotation.stitching import StitchingError
from annotation.stitching_review import DEFAULT_CROP, build_review


class Command(BaseCommand):
    help = "Stitching phase 4: write an offline yes/no review page for candidates.csv."

    def add_arguments(self, parser):
        parser.add_argument(
            "--work", required=True,
            help="Work directory written by stitch_inventory, stitch_ids and stitch_candidates.",
        )
        parser.add_argument(
            "--images", action="append", required=True,
            help="Directory of raw image tiles; repeat for several directories.",
        )
        parser.add_argument(
            "--out", default=None,
            help="Output folder (default: <work>/review, or <work>/review_<i>of<n> with --shard).",
        )
        parser.add_argument(
            "--crop", type=int, default=DEFAULT_CROP,
            help=f"Crop size in pixels (default {DEFAULT_CROP}).",
        )
        parser.add_argument(
            "--shard", default=None,
            help="Only shard i of n, e.g. 2/3: a contiguous, deterministic share of candidates.csv.",
        )
        parser.add_argument(
            "--overwrite", action="store_true",
            help="Replace index.html and crops/ in an existing output folder.",
        )

    def handle(self, *args, **opts):
        try:
            result = build_review(opts["work"], opts["images"], opts["out"], crop=opts["crop"],
                                  shard=opts["shard"], overwrite=opts["overwrite"])
        except StitchingError as exc:
            raise CommandError(str(exc))

        status = Counter(row["status"] for row in result.pairing)
        for row in result.pairing:
            if row["status"] == "no_image":
                self.stderr.write(self.style.WARNING(f"no raw image for tile {row['tile']}"))
            elif row["status"] == "no_prediction":
                self.stderr.write(self.style.WARNING(f"raw image {row['image_path']} matches no prediction tile"))
        self.stderr.write(
            f"{status['matched']} tile(s) paired, {status['no_image']} without image, "
            f"{status['no_prediction']} image(s) without prediction; see image_pairing.csv"
        )
        self.stderr.write(f"{len(result.cards)} card(s) -> {result.out / 'index.html'}")
        self.stderr.write(self.style.SUCCESS("ok"))
