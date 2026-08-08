"""The submit ↔ review loop: durable channel rounds, approve+lock.

Covers acceptance rows A–D of
``progress/history/05-submit-people-hardcases.md``:

* **A** Submit stays available after previous submits and rejects.
* **B** Re-submitting voids the previous same-channel pending row.
* **C** Approve installs the immutable candidate and applies the manager's
  "allow further annotation" choice.
* **D** Reject / revision hand the task back, unlocked, with nothing promoted.
"""

import os
import tempfile

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import AnnotatorProfile, UserProfile
from annotation.label_paths import working_label_rel_path
from annotation.models import AnnotationSubmission, AnnotationTask, ReviewRecord
from annotation.services import (
    approve_submission,
    can_submit_task,
    reject_submission,
    request_revision,
    submit_annotation,
    submit_inapp_annotation,
)
from annotation.visualization import slice_io
from core.choices import LabelType, TaskStatus, TaskType, UserRole
from projects.services import create_project
from volumes.models import Volume

User = get_user_model()
_TMP = tempfile.mkdtemp(prefix="mito-submit-loop-")


# Submission and review history is durable under every feature profile. The
# setting remains only for compatibility with older deployments.
@override_settings(MITO_DATA_ROOT=_TMP, MEDIA_ROOT=_TMP, FEATURE_REVIEW_HISTORY=False)
class SubmitLoopTests(TestCase):
    def setUp(self):
        slice_io.clear_caches()
        self.manager = self._user("sl_mgr", UserRole.MANAGER)
        self.annotator = self._user("sl_ann", UserRole.ANNOTATOR, annotator=True)
        self.other = self._user("sl_other", UserRole.ANNOTATOR, annotator=True)

        self.project = create_project(title="SL", created_by=self.manager, reviewed=True)
        rel = "images/sl.tif"
        path = os.path.join(_TMP, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tifffile.imwrite(path, np.full((4, 8, 8), 100, dtype=np.uint8))
        self.volume = Volume.objects.create(
            project=self.project, name="v", image_path=rel,
            label_type=LabelType.NONE, shape_z=4, shape_y=8, shape_x=8,
        )
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume, assigned_to=self.annotator,
            z_start=0, z_end=4, y_end=8, x_end=8,
            task_type=TaskType.MANUAL_ANNOTATION, status=TaskStatus.ASSIGNED,
        )
        self._write_working_copy()

    def _user(self, name, role, annotator=False):
        user = User.objects.create_user(name, password="x")
        UserProfile.objects.filter(user=user).update(role=role)
        if annotator:
            AnnotatorProfile.objects.create(user=user, is_active_annotator=True)
        return User.objects.get(pk=user.pk)

    def _write_working_copy(self):
        """In-app submit refuses to run with no working copy — give it one."""
        rel = working_label_rel_path(self.volume)
        path = os.path.join(_TMP, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mask = np.zeros((4, 8, 8), dtype=np.uint16)
        mask[0, 0:3, 0:3] = 5
        tifffile.imwrite(path, mask)
        return path

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def _submit(self, user=None):
        return self._client(user or self.annotator).post(
            f"/api/tasks/{self.task.id}/submit-inapp/", {}, format="json"
        )

    # --- A: Submit never disappears while the task is still theirs ----------

    def test_submit_stays_available_after_a_previous_submit(self):
        self.assertEqual(self._submit().status_code, 201)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.SUBMITTED)
        # The old status-list gate hid Submit here; the lock-based gate does not.
        self.assertTrue(can_submit_task(self.annotator, self.task))
        self.assertEqual(self._submit().status_code, 201)

    def test_task_serializer_reports_can_submit(self):
        body = self._client(self.annotator).get(f"/api/tasks/{self.task.id}/").json()
        self.assertTrue(body["can_submit"])
        self.assertTrue(body["can_annotate"])
        self.assertFalse(body["annotation_locked"])

        self._submit()
        body = self._client(self.annotator).get(f"/api/tasks/{self.task.id}/").json()
        self.assertEqual(body["status"], TaskStatus.SUBMITTED)
        self.assertTrue(body["can_submit"], "Submit must survive a submit")
        self.assertEqual(body["submission_count"], 1)

    def test_unassigned_annotator_still_cannot_submit(self):
        self.assertFalse(can_submit_task(self.other, self.task))
        self.assertEqual(self._submit(self.other).status_code, 403)

    # --- B: latest pending submission wins within one channel ---------------

    def test_resubmitting_replaces_the_previous_submission(self):
        first = submit_inapp_annotation(task=self.task, annotator=self.annotator)
        second = submit_inapp_annotation(task=self.task, annotator=self.annotator)
        remaining = list(AnnotationSubmission.objects.filter(task=self.task))
        self.assertEqual({s.id for s in remaining}, {first.id, second.id})
        first.refresh_from_db()
        self.assertEqual(first.review_status, "voided")
        self.assertIsNotNone(first.superseded_at)
        self.task.refresh_from_db()
        self.assertEqual(self.task.submission_count, 2)

    def test_superseded_upload_file_is_retained_for_audit(self):
        first = submit_annotation(
            task=self.task,
            annotator=self.annotator,
            label_file=SimpleUploadedFile("a.tif", b"II*\x00"),
        )
        first_path = first.label_file.path
        self.assertTrue(os.path.exists(first_path))

        submit_annotation(
            task=self.task,
            annotator=self.annotator,
            label_file=SimpleUploadedFile("b.tif", b"II*\x00"),
        )
        self.assertTrue(os.path.exists(first_path))
        first.refresh_from_db()
        self.assertEqual(first.review_status, "voided")

    def test_review_queue_shows_only_the_latest_submission(self):
        submit_inapp_annotation(task=self.task, annotator=self.annotator)
        latest = submit_inapp_annotation(task=self.task, annotator=self.annotator)
        rows = self._client(self.manager).get("/api/submissions/").json()
        self.assertEqual([r["id"] for r in rows], [latest.id])

    def test_the_decision_log_outlives_the_submission_it_decided(self):
        first = submit_inapp_annotation(task=self.task, annotator=self.annotator)
        reject_submission(first, reviewer=self.manager, comments="redo the top")
        submit_inapp_annotation(task=self.task, annotator=self.annotator)

        review = ReviewRecord.objects.get(task=self.task)
        self.assertEqual(review.submission_id, first.id)
        self.assertEqual(review.comments, "redo the top")

    # --- C: approve merges, and the lock switch decides what happens next ----

    def test_approve_without_allowing_further_annotation_locks_the_task(self):
        submission = submit_inapp_annotation(task=self.task, annotator=self.annotator)
        approve_submission(submission, reviewer=self.manager)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.APPROVED)
        self.assertTrue(self.task.annotation_locked)
        self.assertFalse(can_submit_task(self.annotator, self.task))

        # Both the submit and the paint endpoints must 403, not merely hide.
        self.assertEqual(self._submit().status_code, 403)
        put = self._client(self.annotator).put(
            f"/api/tasks/{self.task.id}/label-ids/",
            {"axis": "z", "index": 0, "shape": [8, 8], "runs": [[0, 64]]},
            format="json",
        )
        self.assertEqual(put.status_code, 403, put.content)

    def test_approve_installs_snapshot_and_reseeds_working_mask(self):
        submission = submit_inapp_annotation(task=self.task, annotator=self.annotator)
        approve_submission(submission, reviewer=self.manager)
        self.volume.refresh_from_db()
        self.assertIn("/approved/", self.volume.label_path)
        self.assertNotEqual(self.volume.label_path, working_label_rel_path(self.volume))
        self.assertEqual(self.volume.label_type, LabelType.PARTIAL)
        np.testing.assert_array_equal(
            tifffile.imread(os.path.join(_TMP, self.volume.label_path)),
            tifffile.imread(os.path.join(_TMP, working_label_rel_path(self.volume))),
        )

    def test_approve_with_allow_further_annotation_keeps_the_task_open(self):
        submission = submit_inapp_annotation(task=self.task, annotator=self.annotator)
        approve_submission(
            submission, reviewer=self.manager, allow_further_annotation=True
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.APPROVED)
        self.assertFalse(self.task.annotation_locked)
        self.assertTrue(can_submit_task(self.annotator, self.task))

        # A further submit starts another review round.
        self.assertEqual(self._submit().status_code, 201)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.SUBMITTED)

    def test_review_endpoint_passes_the_switch_through(self):
        submission = submit_inapp_annotation(task=self.task, annotator=self.annotator)
        resp = self._client(self.manager).post(
            f"/api/submissions/{submission.id}/review/",
            {"decision": "approved", "allow_further_annotation": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.task.refresh_from_db()
        self.assertFalse(self.task.annotation_locked)

    def test_manager_can_reopen_a_locked_task(self):
        submission = submit_inapp_annotation(task=self.task, annotator=self.annotator)
        approve_submission(submission, reviewer=self.manager)
        resp = self._client(self.manager).post(
            f"/api/tasks/{self.task.id}/annotation-lock/",
            {"locked": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["can_submit"])
        self.assertEqual(self._submit().status_code, 201)

    def test_annotator_cannot_flip_the_lock(self):
        resp = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/annotation-lock/",
            {"locked": False},
            format="json",
        )
        self.assertIn(resp.status_code, (401, 403))

    # --- D: reject / revision hand the task back ----------------------------

    def test_reject_reopens_the_task_and_promotes_nothing(self):
        submission = submit_inapp_annotation(task=self.task, annotator=self.annotator)
        reject_submission(submission, reviewer=self.manager, comments="too coarse")
        self.task.refresh_from_db()
        self.volume.refresh_from_db()

        self.assertEqual(self.task.status, TaskStatus.REJECTED)
        self.assertFalse(self.task.annotation_locked)
        self.assertTrue(can_submit_task(self.annotator, self.task))
        self.assertEqual(self.volume.label_path, "", "reject must not promote")
        self.assertEqual(self.task.last_decision, "rejected")
        self.assertEqual(self.task.last_decision_comments, "too coarse")
        self.assertEqual(self._submit().status_code, 201)

    def test_revision_requested_behaves_the_same_way(self):
        submission = submit_inapp_annotation(task=self.task, annotator=self.annotator)
        request_revision(submission, reviewer=self.manager)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.REVISION_REQUESTED)
        self.assertFalse(self.task.annotation_locked)
        self.assertEqual(self._submit().status_code, 201)

    def test_reject_new_round_after_an_open_approve_keeps_task_unlocked(self):
        submission = submit_inapp_annotation(task=self.task, annotator=self.annotator)
        approve_submission(
            submission, reviewer=self.manager, allow_further_annotation=True
        )
        self.task.refresh_from_db()
        self.assertFalse(self.task.annotation_locked)

        next_submission = submit_inapp_annotation(
            task=self.task, annotator=self.annotator
        )
        reject_submission(next_submission, reviewer=self.manager)
        self.task.refresh_from_db()
        self.assertFalse(self.task.annotation_locked)
