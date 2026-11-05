"""Stitching phase 2: assign globally unique label IDs across tiles.

Reads tiles.csv from the work directory, streams every tile once to count its
labels, and writes tile_ids.csv (per-tile offsets) and id_map.csv
(tile, local_id, global_id, voxel_count). Offsets are deterministic: tiles in
(z0, y0, x0) order, each offset by the previous tiles' largest local IDs.
With --regions, region masks are paired to tiles by box and each label's
voxels inside the region are counted in the same read (region_voxels).
See docs/engineering/stitching.md.

    python manage.py stitch_ids --work stitch_p10
    python manage.py stitch_ids --work stitch_p10 --regions /data/nag_p10/regions
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from annotation.stitching import StitchingError, run_ids


class Command(BaseCommand):
    help = "Stitching phase 2: write tile_ids.csv and id_map.csv with uint64-range global IDs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--work", required=True,
            help="Work directory written by stitch_inventory.",
        )
        parser.add_argument(
            "--regions", action="append", default=None,
            help="Directory of region masks (nonzero = inside), paired to tiles by box; repeatable.",
        )

    def handle(self, *args, **opts):
        try:
            tile_rows, id_rows, pairing = run_ids(opts["work"], opts["regions"])
        except StitchingError as exc:
            raise CommandError(str(exc))

        top = max((r["id_offset"] + r["max_local_id"] for r in tile_rows), default=0)
        self.stderr.write(
            f"{len(id_rows)} label(s) in {len(tile_rows)} tile(s); largest global id {top}"
        )
        if pairing is not None:
            for row in pairing:
                if row["status"] == "no_region":
                    self.stderr.write(self.style.WARNING(f"no region file for tile {row['tile']}; counted as outside"))
                elif row["status"] == "no_tile":
                    self.stderr.write(self.style.WARNING(f"region file {row['region_path']} matches no tile"))
            status = Counter(row["status"] for row in pairing)
            inside = sum(1 for r in id_rows if r["region_voxels"])
            self.stderr.write(
                f"{status['matched']} tile(s) with a region, {status['no_region']} without, "
                f"{status['no_tile']} region file(s) without a tile; {inside} label(s) reach into the region; "
                "see region_pairing.csv"
            )
        self.stderr.write(self.style.SUCCESS("ok"))
