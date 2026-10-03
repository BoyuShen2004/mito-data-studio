"""Measure per-mitochondrion volume and skeleton length for one volume.

Read-only: nothing in the database or the label files is changed. Measures
the official (approved) label by default; --working measures the annotator's
working copy instead. Skeleton length is the TEASAR cable length (see
annotation.measurements).

    python manage.py measure_mito --volume 12                  # CSV to stdout
    python manage.py measure_mito --volume 12 --out mito_12.csv
    python manage.py measure_mito --volume 12 --working
"""

import csv
import io

from django.core.management.base import BaseCommand, CommandError

from annotation.label_paths import working_label_rel_path
from annotation.measurement_spacing import measurement_spacing
from annotation.measurements import MeasurementError, measure_label_volume
from annotation.visualization.slice_io import resolve_path
from volumes.models import Volume

FIELDS = ("label_id", "voxel_count", "volume_um3", "skeleton_length_um")


class Command(BaseCommand):
    help = "Measure each mitochondrion's volume and skeleton length as CSV."

    def add_arguments(self, parser):
        parser.add_argument(
            "--volume", type=int, required=True,
            help="Volume id to measure.",
        )
        parser.add_argument(
            "--out", default=None,
            help="Write the CSV to this path instead of stdout.",
        )
        parser.add_argument(
            "--working", action="store_true",
            help="Measure the working label copy instead of the official label.",
        )

    def handle(self, *args, **opts):
        try:
            volume = Volume.objects.get(pk=opts["volume"])
        except Volume.DoesNotExist:
            raise CommandError(f"No volume with id {opts['volume']}.")

        voxel_size = tuple(value * 1000 if value is not None else None
                           for value in measurement_spacing(volume)["voxel_size_um_zyx"])
        if any(v is None for v in voxel_size):
            raise CommandError(
                f"Volume {volume.pk} has no voxel size set: (z, y, x) = {voxel_size}. "
                "Set it on the volume before measuring."
            )

        path = self._label_path(volume, working=opts["working"])

        try:
            results = measure_label_volume(path, voxel_size)
        except MeasurementError as exc:
            raise CommandError(str(exc))

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(FIELDS)
        for r in results:
            writer.writerow([
                r.label_id,
                r.voxel_count,
                f"{r.volume_um3:.6g}",
                f"{r.skeleton_length_um:.6g}",
            ])

        if opts["out"]:
            with open(opts["out"], "w", newline="") as fh:
                fh.write(buf.getvalue())
        else:
            self.stdout.write(buf.getvalue(), ending="")

        source = "working copy" if opts["working"] else "official label"
        self.stderr.write(
            f"measured {len(results)} label(s) in volume {volume.pk} "
            f"from the {source}: {path}"
        )
        self.stderr.write(self.style.SUCCESS("ok"))

    def _label_path(self, volume, *, working):
        if working:
            path = resolve_path(working_label_rel_path(volume))
            if not path.exists():
                raise CommandError(f"Volume {volume.pk} has no working label copy.")
            return path
        if not volume.has_label:
            raise CommandError(f"Volume {volume.pk} has no official label registered.")
        return resolve_path(volume.label_location)
