"""Application-owned artifacts must land readable on the host.

``docs/deployment.md`` states one policy for everything this service writes:
directories ``2775``, regular files ``0664``, inherited through ``UMask=0002``
and a setgid data root. ``settings.FILE_UPLOAD_PERMISSIONS`` encodes the file
half, and ``visualization/slice_io.py`` already applies it to lock files.

Atomic writes were the hole. ``tempfile.NamedTemporaryFile`` creates at
``0600`` no matter the umask — the right default for a scratch file — and
``os.replace`` then carries that mode into the *final* artifact. The result is
invisible until somebody on the host tries to read a sidecar and cannot: in
production on 2026-09-06, 44 ``*_mask_metadata.json`` files were ``0600``.

These tests pin the modes rather than the mechanism, so a future rewrite of the
staging strategy is free as long as the artifact still lands ``0664``.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from annotation.cellable_port.label_state import LabelMetadataStore
from core.data_root import apply_owned_file_mode


def mode_of(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class OwnedFileModeTests(TestCase):
    def test_helper_applies_the_documented_file_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "artifact.bin"
            # Exactly how NamedTemporaryFile leaves a staged file.
            target.touch(mode=0o600)
            self.assertEqual(mode_of(target), 0o600)

            apply_owned_file_mode(target)
            self.assertEqual(mode_of(target), 0o664)

    @override_settings(FILE_UPLOAD_PERMISSIONS=0o640)
    def test_helper_follows_the_configured_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "artifact.bin"
            target.touch(mode=0o600)
            apply_owned_file_mode(target)
            self.assertEqual(mode_of(target), 0o640)

    def test_helper_never_widens_to_world_writable(self):
        # 0777 is called out as forbidden in docs/deployment.md: these files are
        # shared infrastructure, and the API's project/team/assignment checks —
        # not the filesystem — are the authorization boundary for writes.
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "artifact.bin"
            target.touch(mode=0o600)
            apply_owned_file_mode(target)
            self.assertFalse(mode_of(target) & stat.S_IWOTH)

    def test_a_chmod_that_cannot_succeed_does_not_fail_the_write(self):
        # Some network mounts refuse chmod. The bytes are already durable by
        # then, so reduced host readability must not raise.
        apply_owned_file_mode("/nonexistent/path/artifact.bin")

    def test_metadata_sidecar_lands_readable_not_staged_at_0600(self):
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "volume_mask_metadata.json"
            store = LabelMetadataStore()
            store.get_or_create(7)
            store.save(str(sidecar))

            self.assertTrue(sidecar.exists())
            self.assertEqual(mode_of(sidecar), 0o664)

    def test_the_sidecar_backup_is_readable_too(self):
        # The .bak is written by shutil.copy2 from the primary, so it inherits
        # whatever the primary has — which is the point of fixing the primary.
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "volume_mask_metadata.json"
            store = LabelMetadataStore()
            store.get_or_create(7)
            store.save(str(sidecar))
            store.get_or_create(8)
            store.save(str(sidecar))

            backup = Path(f"{sidecar}.bak")
            self.assertTrue(backup.exists())
            self.assertEqual(mode_of(backup), 0o664)
