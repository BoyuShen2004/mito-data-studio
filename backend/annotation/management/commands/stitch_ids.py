"""Stitching phase 2: assign globally unique label IDs across tiles.

Reads tiles.csv from the work directory, streams every tile once to count its
labels, and writes tile_ids.csv (per-tile offsets) and id_map.csv
(tile, local_id, global_id, voxel_count). Offsets are deterministic: tiles in
(z0, y0, x0) order, each offset by the previous tiles' largest local IDs.
See docs/engineering/stitching.md.

    python manage.py stitch_ids --work stitch_p10
"""

from django.core.management.base import BaseCommand, CommandError

from annotation.stitching import StitchingError, run_ids


class Command(BaseCommand):
    help = "Stitching phase 2: write tile_ids.csv and id_map.csv with uint64-range global IDs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--work", required=True,
            help="Work directory written by stitch_inventory.",
        )

    def handle(self, *args, **opts):
        try:
            tile_rows, id_rows = run_ids(opts["work"])
        except StitchingError as exc:
            raise CommandError(str(exc))

        top = max((r["id_offset"] + r["max_local_id"] for r in tile_rows), default=0)
        self.stderr.write(
            f"{len(id_rows)} label(s) in {len(tile_rows)} tile(s); largest global id {top}"
        )
        self.stderr.write(self.style.SUCCESS("ok"))
