"""Stitching phase 6: measure every stitched object.

Reads the work directory (tiles.csv, seams.csv, tile_ids.csv, id_map.csv,
candidates.csv, objects.csv and decisions_resolved.csv from stitch_objects)
and writes measurements.csv: one row per object with volume, TEASAR cable
length from the per-volume measurement code, and the edge flag for the whole
tiled volume. Also writes unresolved_candidates.csv, the unsure/undecided
candidates counted in unresolved_contacts, which stitch_review --only turns
into a page for a second review round. Progress is logged per tile and per
multi-tile object.
See docs/engineering/stitching.md.

    python manage.py stitch_measure --work stitch_p10 --voxel-size-zyx 30 8 8
    python manage.py stitch_measure --work stitch_p10 --voxel-size-zyx 30 8 8 \
        --unproofread-tiles unproofread.txt --max-crop-voxels 256000000
    python manage.py stitch_measure --work stitch_p10 --voxel-size-zyx 30 8 8 --region-only
"""

from django.core.management.base import BaseCommand, CommandError

from annotation.stitching import StitchingError
from annotation.stitching_measure import DEFAULT_MAX_CROP_VOXELS, run_measure


def _count(text: str) -> int:
    return int(text.replace(",", "").replace("_", ""))


class Command(BaseCommand):
    help = "Stitching phase 6: write measurements.csv for the objects from stitch_objects."

    def add_arguments(self, parser):
        parser.add_argument(
            "--work", required=True,
            help="Work directory written by the earlier stitching phases.",
        )
        parser.add_argument(
            "--voxel-size-zyx", type=float, nargs=3, required=True, metavar=("Z", "Y", "X"),
            help="Voxel size in nm, Z Y X order (nag_p10: 30 8 8).",
        )
        parser.add_argument(
            "--unproofread-tiles", default=None,
            help="Text file of tile names or paths (one per line) whose labels are not proofread.",
        )
        parser.add_argument(
            "--max-crop-voxels", type=_count, default=DEFAULT_MAX_CROP_VOXELS,
            help=f"Skip cable length for objects whose padded crop is larger (default {DEFAULT_MAX_CROP_VOXELS:,}).",
        )
        parser.add_argument(
            "--region-only", action="store_true",
            help="Only objects reaching into the region (needs stitch_ids --regions); "
                 "unresolved_contacts then counts only in-region candidates.",
        )

    def handle(self, *args, **opts):
        def log(message):
            self.stderr.write(message)
            self.stderr.flush()

        try:
            result = run_measure(opts["work"], opts["voxel_size_zyx"], unproofread=opts["unproofread_tiles"],
                                 max_crop_voxels=opts["max_crop_voxels"], region_only=opts["region_only"],
                                 log=log)
        except StitchingError as exc:
            raise CommandError(str(exc))

        c = result.counts
        self.stderr.write(
            f"{c['objects']} object(s) ({c['multi_tile']} multi-tile) in {c['seconds']}s: "
            f"{c['touches_edge']} touch the volume edge, {c['skipped']} skipped (crop_limit), "
            f"{c['unresolved']} with unresolved contacts, {c['unproofread']} in unproofread tiles"
            + (f"; region only, {c['crosses_region']} cross the region boundary" if c["region_only"] else "")
        )
        self.stderr.write(
            f"{len(result.unresolved)} unresolved candidate(s) -> unresolved_candidates.csv "
            "(stitch_review --only builds a page for them)"
        )
        self.stderr.write(self.style.SUCCESS("ok"))
