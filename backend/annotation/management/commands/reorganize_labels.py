"""One-time-per-scheme-change migration: relocate a volume's existing owned
label file to wherever ``annotation.label_paths.working_label_rel_path``
currently says it should live.

Read ``progress/history/04-incident-data-safety.md`` before touching this —
this command moves real label files. It is deliberately conservative:

- Dry-run by default; nothing is moved or written to the database unless
  ``--apply`` is given.
- Only ever touches a volume whose ``label_path`` **exactly** matches one of
  the known *legacy* paths this app has, at some point, actually used as an
  owned working/official copy for *that volume's own id* (``_legacy_candidates``
  below) — never a path that merely looks similar, and never anything
  registered by reference to someone else's data (an absolute path is never
  a candidate, so those are never touched, no matter what they're named).
- Skips (does not touch) a volume if the legacy file doesn't actually exist
  on disk, or if something already exists at the computed new path (never
  overwrites).
- Moves the file, then repoints ``label_path`` — in that order, so a failure
  partway through never leaves the database pointing at a path that has no
  file (worst case: the file moved but the DB still says the old path,
  which is safe to re-run and fix; never the reverse).

Extend ``_legacy_candidates`` (never remove from it) the next time the
working-copy layout changes again, so this command keeps working for
volumes still sitting on an even older scheme.
"""

from __future__ import annotations

import os
import re
import shutil

from django.core.management.base import BaseCommand

from annotation.label_paths import working_label_rel_path
from annotation.visualization.slice_io import resolve_path
from volumes.models import Volume


def _legacy_candidates(volume) -> list[str]:
    """Every relative path this app has, at some point, used for a volume's
    owned label copy — oldest first. A volume's ``label_path`` matching one
    of these exactly is what makes this command trust it as "ours to move,"
    regardless of which past scheme created it.
    """
    candidates = [
        # Pre-2026-07-20 scheme: flat, no project/dataset nesting at all.
        f"labels/volume_{volume.id}_labels.tif",
    ]

    # 2026-07-20 scheme (progress/history/14-mask-staging-and-approval.md):
    # nested under labels/, with numeric id prefixes on the project/dataset
    # directory names. Recomputed inline (not imported) since label_paths.py
    # itself has since moved on — this is a frozen snapshot of that formula.
    def _old_slug(value: str, fallback: str) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
        return s[:60].strip("-") or fallback

    project_dir = f"{volume.project_id}-{_old_slug(volume.project.title, 'project')}"
    if volume.dataset_id:
        dataset_dir = f"{volume.dataset_id}-{_old_slug(volume.dataset.name, 'dataset')}"
    else:
        dataset_dir = "_no_dataset"
    candidates.append(f"labels/{project_dir}/{dataset_dir}/volume_{volume.id}_labels.tif")

    return candidates


class Command(BaseCommand):
    help = (
        "Move a volume's owned label file from any known legacy path to "
        "wherever label_paths.working_label_rel_path currently puts it. "
        "Dry-run unless --apply is given."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually move files and update the database (default: dry-run, report only).",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        moved = skipped = errors = 0

        for volume in Volume.objects.select_related("project", "dataset").order_by("id"):
            label_path = volume.label_path
            if not label_path or os.path.isabs(label_path):
                continue  # nothing owned, or a by-reference external path — never touch those

            if label_path not in _legacy_candidates(volume):
                continue  # not a path this app has ever generated for this volume

            new_rel = working_label_rel_path(volume)
            if new_rel == label_path:
                continue  # already at the current canonical location

            old_path = resolve_path(label_path)
            new_path = resolve_path(new_rel)

            if not old_path.exists():
                self.stdout.write(
                    f"  skip volume {volume.id}: legacy path recorded but file "
                    f"missing on disk ({old_path})"
                )
                skipped += 1
                continue
            if new_path.exists():
                self.stderr.write(
                    self.style.WARNING(
                        f"  skip volume {volume.id}: something already exists at "
                        f"the new path ({new_path}) — resolve manually, not touching it"
                    )
                )
                errors += 1
                continue

            self.stdout.write(f"volume {volume.id}: {label_path}  ->  {new_rel}")
            if not apply:
                moved += 1
                continue

            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_path), str(new_path))
            except OSError as exc:
                self.stderr.write(self.style.ERROR(f"  FAILED to move: {exc}"))
                errors += 1
                continue

            volume.label_path = new_rel
            volume.save(update_fields=["label_path"])
            moved += 1

        from annotation.visualization import slice_io

        if apply and moved:
            slice_io.clear_caches()  # any cached reads of the old paths are now stale

        mode = "Applied" if apply else "Dry-run (pass --apply to actually move files)"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: {moved} volume(s) to move, {skipped} skipped "
                f"(no legacy file), {errors} error(s)/conflict(s)."
            )
        )
