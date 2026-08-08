"""One-time migration to the per-volume ``_mask`` on-disk layout.

Relocates a volume's *working* annotation artifacts from the old scheme —
``<dataset>/volume_<id>_labels.tif`` + ``…_labels_metadata.json`` sidecar +
a global ``embeddings/<variant>/volume_<id>/`` silo — to the current one:

    <dataset>/<image stem>_mask.tif
    <dataset>/metadata/<image stem>_mask_metadata.json
    <dataset>/embeddings/<variant>/<image stem>_mask_<axis>_<index>_<mtime>.npy

The embedding filename prefix is the *mask* stem (`working_mask_stem`, i.e.
`<image stem>_mask`), matching exactly what `services._ai_embedding_cache_path`
looks up at request time — mismatching it silently defeats the disk cache and
makes every AI click re-run the encoder.

See ``annotation/label_paths.py`` for the layout and why. This is the
sibling of ``reorganize_labels`` (which moves the *official*, DB-recorded
``label_path``); this command moves the on-disk *working* copy files, which
are discovered by path, not recorded in the DB.

Conservative, same as ``reorganize_labels``:

- Dry-run by default; nothing moves unless ``--apply`` is given.
- Never overwrites an existing new-scheme file (skips with a warning).
- Never touches anything registered by reference to an external absolute
  path — it only ever moves files it finds *inside* ``MITO_DATA_ROOT`` at the
  known legacy relative paths for that volume's own id.
- Moves the label first, then its sidecar, then embeddings — each guarded
  independently so a partial failure is safe to re-run.

The editor also adopts a legacy working copy lazily on first touch (see
``services._adopt_legacy_working_copy``), so running this is an optimisation
/ bulk-cleanup, not a correctness prerequisite — but it also relocates the
embedding cache, which the lazy path does not.
"""

from __future__ import annotations

import shutil

from django.core.management.base import BaseCommand

from annotation.label_paths import (
    image_stem,
    legacy_embeddings_dir_rel_path,
    legacy_working_label_metadata_rel_path,
    legacy_working_label_rel_path,
    volume_embeddings_dir_rel_path,
    working_label_metadata_rel_path,
    working_label_rel_path,
    working_mask_stem,
)
from annotation.visualization.slice_io import resolve_path
from volumes.models import Volume

# EfficientSAM variants that ever produced an embedding silo worth migrating.
_KNOWN_VARIANTS = ("vits", "vitt")


class Command(BaseCommand):
    help = (
        "Migrate on-disk working label / metadata / embedding artifacts from "
        "the old volume_<id>_labels scheme to the per-volume <stem>_mask "
        "layout. Dry-run unless --apply is given."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually move files (default: dry-run, report only).",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        self.apply = apply
        moved = skipped = conflicts = 0

        for volume in Volume.objects.select_related("project", "dataset").order_by("id"):
            m, s, c = self._migrate_volume(volume)
            moved += m
            skipped += s
            conflicts += c

        if apply and moved:
            from annotation.visualization import slice_io

            slice_io.clear_caches()  # cached reads of old paths are now stale

        mode = "Applied" if apply else "Dry-run (pass --apply to actually move files)"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: {moved} artifact(s) moved, {skipped} already-current/absent, "
                f"{conflicts} conflict(s) needing manual resolution."
            )
        )

    # -- per-volume ---------------------------------------------------------

    def _migrate_volume(self, volume) -> tuple[int, int, int]:
        moved = skipped = conflicts = 0

        # 1) working mask
        old = resolve_path(legacy_working_label_rel_path(volume))
        new = resolve_path(working_label_rel_path(volume))
        m, s, c = self._move_one(volume, old, new, "mask")
        moved += m
        skipped += s
        conflicts += c

        # 2) lifecycle metadata sidecar (into metadata/)
        old_meta = resolve_path(legacy_working_label_metadata_rel_path(volume))
        new_meta = resolve_path(working_label_metadata_rel_path(volume))
        m, s, c = self._move_one(volume, old_meta, new_meta, "metadata")
        moved += m
        skipped += s
        conflicts += c

        # 3) embeddings. The filename prefix MUST match what the runtime looks
        #    up (`services._ai_embedding_cache_path` -> `working_mask_stem`),
        #    or every click re-runs the encoder against a cache it can't find.
        stem = working_mask_stem(volume)
        for variant in _KNOWN_VARIANTS:
            new_dir = resolve_path(f"{volume_embeddings_dir_rel_path(volume)}/{variant}")

            # 3a) migrate from the global legacy silo, if it still exists:
            #     embeddings/<variant>/volume_<id>/*.npy -> new_dir/<stem>_*.npy
            old_dir = resolve_path(legacy_embeddings_dir_rel_path(volume, variant))
            if old_dir.is_dir():
                for npy in sorted(old_dir.glob("*.npy")):
                    dest = new_dir / f"{stem}_{npy.name}"
                    m, s, c = self._move_one(volume, npy, dest, f"embedding {npy.name}")
                    moved += m
                    skipped += s
                    conflicts += c
                if self.apply:
                    self._rmdir_if_empty(old_dir)
                    self._rmdir_if_empty(old_dir.parent)  # embeddings/<variant>
                    self._rmdir_if_empty(old_dir.parent.parent)  # embeddings/

            # 3b) self-heal any file already in the new location that a prior
            #     (buggy) migration wrote with the bare image-stem prefix
            #     instead of the working-mask-stem prefix — rename it so the
            #     runtime finds it. Idempotent: a file already on the correct
            #     prefix starts with `<stem>_` and is skipped.
            img_prefix = f"{image_stem(volume)}_"
            correct_prefix = f"{stem}_"
            if new_dir.is_dir() and img_prefix != correct_prefix:
                for npy in sorted(new_dir.glob(f"{image_stem(volume)}_*.npy")):
                    if npy.name.startswith(correct_prefix):
                        continue  # already correct
                    dest = new_dir / (correct_prefix + npy.name[len(img_prefix):])
                    m, s, c = self._move_one(
                        volume, npy, dest, f"embedding re-prefix {npy.name}"
                    )
                    moved += m
                    skipped += s
                    conflicts += c

        return moved, skipped, conflicts

    def _move_one(self, volume, old, new, label: str) -> tuple[int, int, int]:
        if not old.exists():
            return 0, 1, 0
        if old.resolve() == new.resolve():
            return 0, 1, 0
        if new.exists():
            self.stderr.write(
                self.style.WARNING(
                    f"  skip volume {volume.id} {label}: target already exists "
                    f"({new}) — resolve manually, not overwriting"
                )
            )
            return 0, 0, 1
        self.stdout.write(f"volume {volume.id} {label}: {old}  ->  {new}")
        if not self.apply:
            return 1, 0, 0
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
        except OSError as exc:
            self.stderr.write(self.style.ERROR(f"  FAILED to move {label}: {exc}"))
            return 0, 0, 1
        return 1, 0, 0

    @staticmethod
    def _rmdir_if_empty(path) -> None:
        try:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass
