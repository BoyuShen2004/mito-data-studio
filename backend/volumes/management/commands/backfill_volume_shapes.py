"""Re-read the shape (and voxel size) of volumes registered without one.

    python manage.py backfill_volume_shapes            # report only
    python manage.py backfill_volume_shapes --apply    # write what it reads
    python manage.py backfill_volume_shapes --apply --project 11

Why this exists: shape autodetection runs once, at registration, and is
deliberately best-effort — a header it cannot read must not fail the
registration. But a volume with no ``shape_z`` is skipped by
``ensure_volume_tasks``, so it silently becomes a volume no manager can turn
into a task, and nothing in the UI says why.

The common cause is not a bad file. Volumes are registered *by reference* to
data this app does not own, and the service account is not the account that
wrote it: a ``0640`` source file inside a world-listable directory scans
perfectly and then reads as "no shape". Once the ACL is granted, the files are
readable but the database still says nothing — this command is the second look.

Read-only against the source data: it opens headers and writes only the
``shape_*``/``voxel_size_*`` columns of rows that were blank. Existing values
are never overwritten, so a manually corrected shape is safe.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Q

from volumes.models import Volume
from volumes.services import (
    _unreadable_reason,
    ensure_volume_shape,
    volume_image_file,
)


class Command(BaseCommand):
    help = "Fill in missing volume shapes by re-reading the source image headers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Write the detected shapes. Without this, only reports.",
        )
        parser.add_argument(
            "--project", type=int, default=None,
            help="Limit to one project id.",
        )

    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        volumes = Volume.objects.filter(
            Q(shape_z__isnull=True) | Q(shape_y__isnull=True) | Q(shape_x__isnull=True)
            | Q(voxel_size_z__isnull=True) | Q(voxel_size_y__isnull=True)
            | Q(voxel_size_x__isnull=True)
        ).order_by("pk")
        if options["project"] is not None:
            volumes = volumes.filter(project_id=options["project"])

        total = volumes.count()
        if not total:
            self.stdout.write("Every volume already has complete shape and voxel metadata.")
            return

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"{total} volume(s) with no recorded shape"
                + ("" if apply_changes else "  (dry run — pass --apply to write)")
            )
        )

        fixed = 0
        still_unreadable = []
        for volume in volumes:
            path = volume_image_file(volume)
            if path is None:
                still_unreadable.append((volume, "no image is registered"))
                continue
            if apply_changes:
                ok = ensure_volume_shape(volume)
                shape = (volume.shape_x, volume.shape_y, volume.shape_z)
            else:
                # Same reader, no write — so a dry run reports exactly what an
                # --apply would record.
                from core.utils import inspect_volume_shape

                detected = inspect_volume_shape(path)
                ok = detected is not None
                shape = detected or (None, None, None)
            if ok:
                fixed += 1
                self.stdout.write(
                    f"  {self.style.SUCCESS('shape')} volume {volume.pk} "
                    f"{volume.name}: x={shape[0]} y={shape[1]} z={shape[2]}"
                )
            else:
                still_unreadable.append((volume, _unreadable_reason(path)))

        self.stdout.write("")
        self.stdout.write(
            f"{fixed} readable, {len(still_unreadable)} still without a shape."
        )
        for volume, reason in still_unreadable:
            self.stdout.write(
                self.style.WARNING(f"  volume {volume.pk} {volume.name}: {reason}")
            )
        if fixed and not apply_changes:
            self.stdout.write(
                "\nRe-run with " + self.style.WARNING("--apply") + " to record these."
            )
        if fixed and apply_changes:
            self.stdout.write(
                "\nRun the project's assignment plan (or auto-assign) to turn "
                "these volumes into tasks."
            )
