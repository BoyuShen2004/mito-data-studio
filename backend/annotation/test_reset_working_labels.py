"""Reset a task's *working* annotation back to the registered label mask.

The working copy is a draft forked from the registered mask; this is the one
supported way to throw that draft away. The invariant every test here defends is
that the registered source is only ever **read** — losing it would lose the only
thing a reset can restore from.
"""

import tempfile
from pathlib import Path

import numpy as np
import tifffile
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import UserProfile
from core.choices import UserRole

from annotation.services import (
    create_whole_volume_task,
    get_label_slice_ids,
    registered_label_location,
    reset_working_labels_to_registered,
    set_label_slice_ids,
    upsert_tracking_prompt,
    list_tracking_prompts,
)
from annotation.label_paths import (
    working_label_metadata_rel_path,
    working_label_rel_path,
)
from annotation.visualization.slice_io import decode_label_rle, encode_label_rle, resolve_path
from projects.models import Dataset, Project
from volumes.models import Volume


class ResetWorkingLabelsTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.override = override_settings(MITO_DATA_ROOT=self.root)
        self.override.enable()
        self.manager = User.objects.create_superuser("reset-manager", password="x")
        self.annotator = User.objects.create_user("reset-annotator", password="x")
        self.other = User.objects.create_user("reset-outsider", password="x")
        for user in (self.annotator, self.other):
            UserProfile.objects.update_or_create(
                user=user, defaults={"role": UserRole.ANNOTATOR}
            )
        project = Project.objects.create(title="Reset project", created_by=self.manager)
        dataset = Dataset.objects.create(project=project, name="Reset data")
        tifffile.imwrite(self.root / "image.tif", np.zeros((2, 4, 4), dtype=np.uint8))
        # The registered mask: label 3 fills the top-left quadrant of z=0.
        registered = np.zeros((2, 4, 4), dtype=np.uint16)
        registered[0, :2, :2] = 3
        tifffile.imwrite(self.root / "registered.tif", registered)
        self.registered = registered
        self.volume = Volume.objects.create(
            project=project,
            dataset=dataset,
            name="Reset volume",
            image_path="image.tif",
            label_path="registered.tif",
            shape_z=2,
            shape_y=4,
            shape_x=4,
        )
        self.task = create_whole_volume_task(self.volume)
        self.task.assigned_to = self.annotator
        self.task.save(update_fields=["assigned_to"])

    def tearDown(self):
        self.override.disable()
        self.tmp.cleanup()

    def _paint(self, value=9):
        """Fork the working copy and paint something the registered mask lacks."""
        set_label_slice_ids(
            self.volume,
            "z",
            0,
            [4, 4],
            encode_label_rle(np.full((4, 4), value, dtype=np.int32)),
        )

    def _plane(self, z=0):
        saved = get_label_slice_ids(self.volume, "z", z)
        return decode_label_rle(saved["runs"], (4, 4))

    def test_reset_restores_the_registered_mask_over_the_draft(self):
        self._paint()
        np.testing.assert_array_equal(self._plane(), np.full((4, 4), 9))

        reset_working_labels_to_registered(self.task)

        np.testing.assert_array_equal(self._plane(), self.registered[0])

    def test_the_registered_source_file_is_never_written(self):
        self._paint()
        before = tifffile.imread(str(self.root / "registered.tif"))

        reset_working_labels_to_registered(self.task)
        self._paint(value=5)
        reset_working_labels_to_registered(self.task)

        np.testing.assert_array_equal(
            tifffile.imread(str(self.root / "registered.tif")), before
        )

    def test_reset_clears_the_lifecycle_sidecar_and_track_queue(self):
        self._paint()
        upsert_tracking_prompt(
            self.task,
            {
                "parent_id": 9,
                "subclasses": [{"index": 1, "seeds": [{"z": 0, "rle": [[0, 4]], "shape": [4, 4]}]}],
                "status": "ready",
            },
        )
        sidecar = resolve_path(working_label_metadata_rel_path(self.volume))
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text('{"labels": {"9": {"state": "verified"}}}')
        backup = sidecar.with_name(f"{sidecar.name}.bak")
        backup.write_text('{"labels": {"9": {"state": "verified"}}}')
        self.assertTrue(sidecar.exists())

        reset_working_labels_to_registered(self.task)

        # Both are removed from the live location, but retained as timestamped
        # recovery snapshots instead of being destroyed.
        self.assertFalse(sidecar.exists())
        self.assertFalse(backup.exists())
        self.assertEqual(len(list(sidecar.parent.glob(f"{sidecar.name}.pre-reset.*.bak"))), 1)
        self.assertEqual(len(list(sidecar.parent.glob(f"{backup.name}.pre-reset.*.bak"))), 1)
        self.volume.refresh_from_db()
        self.task.volume = self.volume
        self.assertEqual(list_tracking_prompts(self.task), [])

    def test_a_volume_registered_without_a_label_resets_to_empty(self):
        self.volume.label_path = ""
        self.volume.save(update_fields=["label_path"])
        self._paint()

        result = reset_working_labels_to_registered(self.task)

        self.assertTrue(result["seeded_empty"])
        np.testing.assert_array_equal(self._plane(), np.zeros((4, 4), dtype=np.int32))

    def test_reset_after_approval_restores_the_registration_not_the_approval(self):
        """Approval repoints `label_path` at the working copy; Reset must not
        then mean "restore the last approved state"."""
        from annotation.services import _repoint_label

        self._paint()
        working_rel = working_label_rel_path(self.volume)
        update_fields = _repoint_label(self.volume, working_rel)
        self.volume.save(update_fields=update_fields)
        self.assertEqual(
            registered_label_location(self.volume), "registered.tif"
        )

        reset_working_labels_to_registered(self.task)

        np.testing.assert_array_equal(self._plane(), self.registered[0])

    def test_a_volume_whose_label_is_already_the_working_copy_refuses(self):
        """No recorded registration and label_path == the working copy: there is
        nothing distinct to restore, and deleting the file first would destroy
        the only mask there is."""
        self._paint()
        self.volume.label_path = working_label_rel_path(self.volume)
        self.volume.save(update_fields=["label_path"])

        result = reset_working_labels_to_registered(self.task)

        # Treated as "registered with no label", i.e. reset to empty — never as
        # "seed this file from itself".
        self.assertTrue(result["seeded_empty"])
        np.testing.assert_array_equal(self._plane(), np.zeros((4, 4), dtype=np.int32))


class ResetWorkingLabelsApiTests(ResetWorkingLabelsTests):
    def _url(self):
        return reverse("api-task-labels-reset", args=[self.task.pk])

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_the_assigned_annotator_may_reset_their_own_task(self):
        self._paint()
        response = self._client(self.annotator).post(self._url(), {"confirm": True}, format="json")
        self.assertEqual(response.status_code, 200)
        np.testing.assert_array_equal(self._plane(), self.registered[0])

    def test_a_manager_may_reset_someone_elses_task(self):
        self._paint()
        response = self._client(self.manager).post(self._url(), {"confirm": True}, format="json")
        self.assertEqual(response.status_code, 200)
        np.testing.assert_array_equal(self._plane(), self.registered[0])

    def test_an_unrelated_annotator_may_not(self):
        self._paint()
        response = self._client(self.other).post(self._url(), {"confirm": True}, format="json")
        self.assertEqual(response.status_code, 403)
        np.testing.assert_array_equal(self._plane(), np.full((4, 4), 9))

    def test_an_approved_and_locked_task_is_refused(self):
        self._paint()
        self.task.annotation_locked = True
        self.task.save(update_fields=["annotation_locked"])
        response = self._client(self.manager).post(self._url(), {"confirm": True}, format="json")
        self.assertEqual(response.status_code, 403)
        np.testing.assert_array_equal(self._plane(), np.full((4, 4), 9))

    def test_confirm_is_required(self):
        self._paint()
        response = self._client(self.annotator).post(self._url(), {}, format="json")
        self.assertEqual(response.status_code, 400)
        np.testing.assert_array_equal(self._plane(), np.full((4, 4), 9))

    def test_get_is_not_a_reset(self):
        self._paint()
        response = self._client(self.annotator).get(self._url())
        self.assertEqual(response.status_code, 405)
        np.testing.assert_array_equal(self._plane(), np.full((4, 4), 9))
