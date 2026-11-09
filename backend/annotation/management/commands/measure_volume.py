"""Measure mitochondria in one large, already-stitched Neuroglancer volume.

Reads a Neuroglancer precomputed segmentation (scale 0, unsharded) and an
optional region mask on the same grid, visiting only the chunk files that
exist, and writes the same delivery files as stitch_deliver
(mitochondria.csv, summary.csv, excluded.csv, README.md) into
<work>/delivery, plus <work>/label_stats.csv (pass 1, every label) and
<work>/measure_run.json (all settings). Cable length uses the per-volume
measurement code unchanged. The voxel size is required and overrides the
volume's metadata (recorded when they disagree).
See docs/engineering/stitching.md ("Measuring an upstream-stitched volume").

    python manage.py measure_volume --labels /data/p7/labels --region /data/p7/region_pc \\
        --voxel-size-zyx 30 8 8 --work /scratch/measure_p7 --region-only
    python manage.py measure_volume --labels /data/p7/labels --voxel-size-zyx 30 8 8 \\
        --work /scratch/measure_p7 --exclude-label-ids not_mito.txt --overwrite
"""

import shlex

from django.core.management.base import BaseCommand, CommandError

from annotation.stitching import StitchingError
from annotation.stitching_deliver import DELIVERY_FILES
from annotation.stitching_measure import DEFAULT_MAX_CROP_VOXELS
from annotation.volume_measure import run_measure_volume


def _count(text: str) -> int:
    return int(text.replace(",", "").replace("_", ""))


def _command_line(opts) -> str:
    """The run's options as a measure_volume command line, for measure_run.json and the README."""
    args = ["measure_volume", "--labels", str(opts["labels"])]
    if opts["region"]:
        args += ["--region", str(opts["region"])]
    args += ["--voxel-size-zyx", *(f"{v:g}" for v in opts["voxel_size_zyx"]), "--work", str(opts["work"])]
    if opts["max_crop_voxels"] != DEFAULT_MAX_CROP_VOXELS:
        args += ["--max-crop-voxels", str(opts["max_crop_voxels"])]
    if opts["region_only"]:
        args.append("--region-only")
    if opts["exclude_label_ids"]:
        args += ["--exclude-label-ids", str(opts["exclude_label_ids"])]
    if opts["volume_name"]:
        args += ["--volume-name", opts["volume_name"]]
    if opts["out"]:
        args += ["--out", str(opts["out"])]
    return shlex.join(args)


class Command(BaseCommand):
    help = ("Measure mitochondria in one already-stitched Neuroglancer precomputed volume and write the "
            "stitch_deliver delivery files.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--labels", required=True,
            help="Neuroglancer precomputed segmentation directory (holding 'info'); scale 0 is read.",
        )
        parser.add_argument(
            "--region", default=None,
            help="Optional precomputed region mask on the same voxel grid (nonzero = inside).",
        )
        parser.add_argument(
            "--voxel-size-zyx", type=float, nargs=3, required=True, metavar=("Z", "Y", "X"),
            help="Voxel size in nm, Z Y X order (p7: 30 8 8). Overrides the volume's metadata.",
        )
        parser.add_argument(
            "--work", required=True,
            help="Work directory for label_stats.csv, measure_run.json and the delivery folder.",
        )
        parser.add_argument(
            "--max-crop-voxels", type=_count, default=DEFAULT_MAX_CROP_VOXELS,
            help=f"Skip cable length for labels whose padded crop is larger (default {DEFAULT_MAX_CROP_VOXELS:,}).",
        )
        parser.add_argument(
            "--region-only", action="store_true",
            help="Measure and list only labels with voxels inside the region (needs --region).",
        )
        parser.add_argument(
            "--exclude-label-ids", default=None, metavar="FILE",
            help="Text file of label IDs (one per line) that reviewers judged not to be mitochondria; "
                 "they go to excluded.csv.",
        )
        parser.add_argument(
            "--volume-name", default=None,
            help="Name written in the 'tiles' column and the README (default: the labels directory name).",
        )
        parser.add_argument(
            "--out", default=None,
            help="Delivery folder (default <work>/delivery).",
        )
        parser.add_argument(
            "--overwrite", action="store_true",
            help=f"Replace {', '.join(DELIVERY_FILES)} in a non-empty delivery folder.",
        )

    def handle(self, *args, **opts):
        def log(message):
            self.stderr.write(message)
            self.stderr.flush()

        try:
            result = run_measure_volume(
                opts["labels"], opts["work"], opts["voxel_size_zyx"], region=opts["region"],
                region_only=opts["region_only"], max_crop_voxels=opts["max_crop_voxels"],
                exclude_label_ids=opts["exclude_label_ids"], name=opts["volume_name"], out=opts["out"],
                overwrite=opts["overwrite"], command=_command_line(opts), log=log)
        except StitchingError as exc:
            raise CommandError(str(exc))

        c = result.counts
        self.stderr.write(
            f"{c['listed']} mitochondria ({c['core']} core) of {c['labels']} label(s), {c['excluded']} excluded: "
            f"{c['cut_off_at_data_edge']} cut off at the data edge, {c['crosses_region_boundary']} cross the "
            f"region boundary, {c['skipped_crop_limit']} skipped (crop_limit) -> {result.out}"
        )
        if not result.run["voxel_size_matches_metadata"]:
            self.stderr.write(f"voxel size override recorded: {result.run['voxel_size_note']}")
        self.stderr.write(self.style.SUCCESS("ok"))
