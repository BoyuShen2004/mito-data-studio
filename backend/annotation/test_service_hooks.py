"""Post-commit hooks on submit, approve, and assign.

Every one of them runs *after* the work it follows is already durable. The
property asserted here is that none of them can turn a successful operation
into a failure — a 500 after a successful submit tells an annotator their work
was lost when it was not.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import AnnotatorProfile, Notification, UserProfile
from annotation.models import AnnotationTask
from annotation.services import (
    approve_submission,
    assign_task_to_annotator,
    submit_annotation,
)
from core.choices import NotificationVerb, TaskType, UserRole
from projects.models import Dataset, Project
from volumes.models import Volume

_TMP = tempfile.mkdtemp(prefix="mito_hooks_")
SETTINGS = override_settings(MITO_DATA_ROOT=_TMP, MEDIA_ROOT=_TMP)


def make_user(username, role):
    user = User.objects.create_user(username=username, password="pw")
    UserProfile.objects.update_or_create(user=user, defaults={"role": role})
    AnnotatorProfile.objects.get_or_create(user=user)
    # Refetch: the profile is auto-created with the default annotator role and
    # cached on the instance, so `update_or_create` changes the row while this
    # object still reports "annotator" — and every `is_manager` check reading
    # it would silently test the wrong role. Same trap documented in
    # volumes/test_chunk_service.py.
    return User.objects.get(pk=user.pk)


def label_upload(name="s.tif"):
    buffer = io.BytesIO()
    tifffile.imwrite(buffer, np.zeros((2, 2, 2), dtype=np.uint16))
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/tiff")


@SETTINGS
class ServiceHookTests(TestCase):
    def setUp(self):
        self.manager = make_user("mgr", UserRole.MANAGER)
        self.annotator = make_user("ann", UserRole.ANNOTATOR)
        self.project = Project.objects.create(title="P", created_by=self.manager)
        self.dataset = Dataset.objects.create(project=self.project, name="D")
        # A real registered image on disk: approval installs the label as
        # official and re-seeds the working copy from it, which needs a shape
        # to reset to.
        image = Path(settings.MITO_DATA_ROOT) / "hooks-image.tif"
        image.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(str(image), np.zeros((2, 2, 2), dtype=np.uint16))
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="V",
            image_path="hooks-image.tif",
            shape_z=2, shape_y=2, shape_x=2,
        )
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume,
            z_start=0, z_end=2, y_end=2, x_end=2,
            task_type=TaskType.MANUAL_ANNOTATION,
        )

    # --- assignment -------------------------------------------------------

    def test_assigning_notifies_the_new_assignee(self):
        assign_task_to_annotator(
            self.task, annotator=self.annotator, actor=self.manager
        )
        rows = Notification.objects.filter(recipient=self.annotator)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().verb, NotificationVerb.TASK_ASSIGNED)

    def test_reassigning_to_the_same_person_does_not_re_notify(self):
        assign_task_to_annotator(
            self.task, annotator=self.annotator, actor=self.manager
        )
        assign_task_to_annotator(
            self.task, annotator=self.annotator, actor=self.manager
        )
        self.assertEqual(
            Notification.objects.filter(recipient=self.annotator).count(), 1
        )

    def test_a_failing_notification_does_not_fail_the_assignment(self):
        with mock.patch(
            "accounts.notifications.notify_task_assigned",
            side_effect=RuntimeError("inbox down"),
        ):
            assign_task_to_annotator(
                self.task, annotator=self.annotator, actor=self.manager
            )
        self.task.refresh_from_db()
        self.assertEqual(self.task.assigned_to_id, self.annotator.pk)

    # --- submit -----------------------------------------------------------

    def test_submitting_notifies_the_managers(self):
        assign_task_to_annotator(
            self.task, annotator=self.annotator, actor=self.manager
        )
        submit_annotation(
            task=self.task, annotator=self.annotator, label_file=label_upload()
        )
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.manager,
                verb=NotificationVerb.SUBMISSION_RECEIVED,
            ).exists()
        )

    def test_the_submitter_is_not_notified_of_their_own_submission(self):
        manager_task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume, assigned_to=self.manager,
            z_start=0, z_end=2, y_end=2, x_end=2,
            task_type=TaskType.MANUAL_ANNOTATION,
        )
        submit_annotation(
            task=manager_task, annotator=self.manager, label_file=label_upload()
        )
        self.assertFalse(
            Notification.objects.filter(recipient=self.manager).exists()
        )

    def test_a_failing_hook_does_not_fail_the_submit(self):
        """The submission row and its snapshot are already durable here."""
        assign_task_to_annotator(
            self.task, annotator=self.annotator, actor=self.manager
        )
        with mock.patch(
            "accounts.notifications.manager_recipients",
            side_effect=RuntimeError("db down"),
        ), mock.patch(
            "annotation.quality_scoring.score_against_gold_standard",
            side_effect=RuntimeError("numpy down"),
        ):
            submission = submit_annotation(
                task=self.task, annotator=self.annotator,
                label_file=label_upload(),
            )
        self.assertIsNotNone(submission.pk)
        self.task.refresh_from_db()
        self.assertEqual(self.task.submission_count, 1)

    # --- approve ----------------------------------------------------------

    def test_reviewing_notifies_the_annotator(self):
        assign_task_to_annotator(
            self.task, annotator=self.annotator, actor=self.manager
        )
        submission = submit_annotation(
            task=self.task, annotator=self.annotator, label_file=label_upload()
        )
        approve_submission(submission, reviewer=self.manager, comments="good")
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.annotator,
                verb=NotificationVerb.SUBMISSION_REVIEWED,
            ).exists()
        )

    def test_a_failing_post_approval_hook_does_not_fail_the_approval(self):
        """The label is already installed as official by this point."""
        assign_task_to_annotator(
            self.task, annotator=self.annotator, actor=self.manager
        )
        submission = submit_annotation(
            task=self.task, annotator=self.annotator, label_file=label_upload()
        )
        with mock.patch(
            "annotation.services._after_approval",
            side_effect=RuntimeError("scoring exploded"),
        ):
            review = approve_submission(submission, reviewer=self.manager)
        self.assertIsNotNone(review.pk)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "approved")

    def test_no_agreement_score_when_the_assignee_own_work_is_approved(self):
        """Nothing was corrected, so there is nothing to measure.

        Writing a perfect score here would claim a measurement the reviewer
        never made.
        """
        assign_task_to_annotator(
            self.task, annotator=self.annotator, actor=self.manager
        )
        submission = submit_annotation(
            task=self.task, annotator=self.annotator, label_file=label_upload()
        )
        with override_settings(FEATURE_QUALITY_METRICS=True):
            approve_submission(submission, reviewer=self.manager)
        self.assertEqual(submission.quality_scores.count(), 0)
