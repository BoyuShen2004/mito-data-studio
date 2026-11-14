"""Export the stitched segmentation as a Neuroglancer precomputed layer.

Reads the work directory (tiles.csv, tile_ids.csv, id_map.csv, objects.csv)
and the tiles, and writes one uint64 segmentation in which every voxel holds
its stitched object's ID (the mito_id of the delivery), at the dataset's voxel
coordinates. Use it to view the stitched result and spot-check seams. Also
writes export_run.json in the work directory. See docs/engineering/stitching.md.

    python manage.py stitch_export --work stitch_p10 --out /scratch/p10_stitched --voxel-size-zyx 30 8 8
    python manage.py stitch_export --work stitch_p10 --out /scratch/p10_region --voxel-size-zyx 30 8 8 --region-only
    python manage.py stitch_export --work stitch_p10 --out /scratch/p10_labels --voxel-size-zyx 30 8 8 --ids label
"""

import shlex

from django.core.management.base import BaseCommand, CommandError

from annotation.stitching import StitchingError
from annotation.stitching_export import DEFAULT_CHUNK_XYZ, ID_MODES, run_export


def _command_line(opts) -> str:
    args = ["stitch_export", "--work", str(opts["work"]), "--out", str(opts["out"]),
            "--voxel-size-zyx", *(f"{v:g}" for v in opts["voxel_size_zyx"])]
    if opts["ids"] != "object":
        args += ["--ids", opts["ids"]]
    if opts["region_only"]:
        args.append("--region-only")
    if tuple(opts["chunk_size_xyz"]) != DEFAULT_CHUNK_XYZ:
        args += ["--chunk-size-xyz", *map(str, opts["chunk_size_xyz"])]
    if opts["overwrite"]:
        args.append("--overwrite")
    return shlex.join(args)


class Command(BaseCommand):
    help = "Write the stitched segmentation (object IDs, uint64) as a Neuroglancer precomputed layer."

    def add_arguments(self, parser):
        parser.add_argument("--work", required=True,
                            help="Work directory written by the stitching phases (through stitch_objects).")
        parser.add_argument("--out", required=True, help="Directory to write the precomputed layer into.")
        parser.add_argument("--voxel-size-zyx", type=float, nargs=3, required=True, metavar=("Z", "Y", "X"),
                            help="Voxel size in nm, Z Y X order (nag_p10: 30 8 8). Written as the layer's resolution.")
        parser.add_argument("--ids", choices=ID_MODES, default="object",
                            help="object (default): each voxel holds its stitched object's ID. "
                                 "label: its own global ID, before merging.")
        parser.add_argument("--region-only", action="store_true",
                            help="Write only objects reaching into the region (needs stitch_ids --regions).")
        parser.add_argument("--chunk-size-xyz", type=int, nargs=3, default=DEFAULT_CHUNK_XYZ, metavar=("X", "Y", "Z"),
                            help="Chunk size in voxels, X Y Z order (default {} {} {}).".format(*DEFAULT_CHUNK_XYZ))
        parser.add_argument("--overwrite", action="store_true",
                            help="Replace an existing precomputed layer in --out.")

    def handle(self, *args, **opts):
        def log(message):
            self.stderr.write(message)
            self.stderr.flush()

        try:
            result = run_export(opts["work"], opts["out"], opts["voxel_size_zyx"], ids=opts["ids"],
                                region_only=opts["region_only"], chunk_xyz=opts["chunk_size_xyz"],
                                overwrite=opts["overwrite"], command=_command_line(opts), log=log)
        except StitchingError as exc:
            raise CommandError(str(exc))

        c = result.counts
        self.stderr.write(f"{c['segments']} segment(s), {c['labelled_voxels']:,} labelled voxel(s) from "
                          f"{c['tiles']} tile(s) in {c['seconds']}s -> {result.out}")
        self.stderr.write(self.style.SUCCESS("ok"))
