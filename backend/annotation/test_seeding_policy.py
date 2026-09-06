"""How a corrupt or mismatched registered label is handled — both halves.

Two functions read a volume's registered label and they take **opposite**
positions on an unreadable one:

* ``_seed_working_label`` — refuses, raising.
* ``_load_or_init_label`` — starts empty.

That is deliberate, and it is safe only because of the order in which they are
reached: the strict one is the editor's entry point, the lenient one runs only
from SAM2 tracking, and tracking cannot happen before the editor has opened.
These tests pin both behaviours so the divergence stays a recorded decision
rather than drifting into a real contradiction.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import tifffile
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from accounts.models import UserProfile
from annotation.models import AnnotationTask
from core.choices import TaskType, UserRole
from projects.models import Dataset, Project
from volumes.models import Volume

SHAPE = (2, 4, 4)


class SeedingPolicyTests(TestCase):
    def setUp(self):
        # A data root **per test**, not per module. The working-copy path is
        # derived from the project/dataset/volume names, which are identical in
        # every test here, so a shared root leaves one test's working copy on
        # disk for the next — and a working copy short-circuits the seeding
        # path entirely, which is exactly what these tests are about. A shared
        # root made three of them silently pass through the branch they meant
        # to exercise.
        self.tmp = tempfile.TemporaryDirectory(prefix="mito_seed_policy_")
        self.addCleanup(self.tmp.cleanup)
        overrides = override_settings(
            MITO_DATA_ROOT=self.tmp.name, MEDIA_ROOT=self.tmp.name
        )
        overrides.enable()
        self.addCleanup(overrides.disable)

        self.root = Path(settings.MITO_DATA_ROOT)
        self.external = self.root / "external"
        self.external.mkdir(parents=True, exist_ok=True)

        self.image = self.external / "seed_image.tif"
        tifffile.imwrite(str(self.image), np.zeros(SHAPE, dtype=np.uint16))

        self.user = User.objects.create_user(username="seed", password="pw")
        UserProfile.objects.update_or_create(
            user=self.user, defaults={"role": UserRole.MANAGER}
        )
        self.project = Project.objects.create(title="P", created_by=self.user)
        self.dataset = Dataset.objects.create(project=self.project, name="D")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="V",
            image_path=str(self.image),
            shape_z=SHAPE[0], shape_y=SHAPE[1], shape_x=SHAPE[2],
        )
        AnnotationTask.objects.create(
            project=self.project, volume=self.volume,
            z_start=0, z_end=SHAPE[0], y_end=SHAPE[1], x_end=SHAPE[2],
            task_type=TaskType.MANUAL_ANNOTATION,
        )

    def point_at(self, label_array=None, corrupt=False):
        path = self.external / "registered_label.tif"
        if corrupt:
            path.write_bytes(b"II*\x00 not a real tiff")
        else:
            tifffile.imwrite(str(path), np.asarray(label_array, dtype=np.uint16))
        self.volume.label_path = str(path)
        self.volume.save(update_fields=["label_path"])
        return path

    # --- the strict half ---------------------------------------------------

    def test_the_editor_refuses_a_corrupt_registered_label(self):
        from annotation.services import _writable_label

        self.point_at(corrupt=True)
        with self.assertRaises(ValueError) as ctx:
            _writable_label(self.volume, SHAPE)
        self.assertIn("refusing to create an empty working copy", str(ctx.exception))

    def test_the_editor_refuses_a_mismatched_shape(self):
        from annotation.services import _writable_label

        self.point_at(np.ones((1, 2, 2), dtype=np.uint16))
        with self.assertRaises(ValueError) as ctx:
            _writable_label(self.volume, SHAPE)
        self.assertIn("does not match image shape", str(ctx.exception))

    def test_a_good_registered_label_is_used_as_the_seed(self):
        from annotation.services import _writable_label

        seed = np.zeros(SHAPE, dtype=np.uint16)
        seed[0, 0, 0] = 5
        self.point_at(seed)
        memmap, _ = _writable_label(self.volume, SHAPE)
        self.assertEqual(int(np.asarray(memmap).max()), 5)

    # --- the lenient half --------------------------------------------------

    def test_tracking_starts_empty_rather_than_raising(self):
        """The lenient branch, asserted directly.

        Reachable only by calling this function without going through the
        editor first — which no production caller does.
        """
        from annotation.services import _load_or_init_label

        self.point_at(corrupt=True)
        out = _load_or_init_label(self.volume, SHAPE)
        self.assertEqual(out.shape, SHAPE)
        self.assertEqual(int(out.max()), 0)

    def test_a_working_copy_wins_over_the_registered_label_entirely(self):
        """Why the divergence never bites: once a working copy exists the
        official label is not read at all, so its state cannot matter."""
        from annotation.services import _load_or_init_label, _writable_label

        good = np.zeros(SHAPE, dtype=np.uint16)
        good[0, 0, 0] = 5
        self.point_at(good)

        memmap, _ = _writable_label(self.volume, SHAPE)
        memmap[1, 1, 1] = 9
        memmap.flush()

        # Now break the registered label behind its back.
        self.point_at(corrupt=True)
        out = _load_or_init_label(self.volume, SHAPE)
        # The annotator's own work, not zeros and not a raise.
        self.assertEqual(int(out[1, 1, 1]), 9)

    def test_the_editor_gate_runs_before_tracking_can(self):
        """The ordering the divergence depends on, stated as a test.

        Tracking needs prompts, prompts need the editor, and the editor calls
        the strict path — so a volume whose registered label is corrupt cannot
        reach tracking with no working copy.
        """
        from annotation.services import _writable_label

        self.point_at(corrupt=True)
        with self.assertRaises(ValueError):
            _writable_label(self.volume, SHAPE)
        # And no working copy was created as a side effect of failing.
        from annotation.label_paths import working_label_rel_path
        from annotation.visualization.slice_io import resolve_path

        self.assertFalse(resolve_path(working_label_rel_path(self.volume)).exists())
