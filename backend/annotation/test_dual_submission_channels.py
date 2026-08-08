import io
import os
import tempfile
import threading
import unittest

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings

from accounts.models import AnnotatorProfile, UserProfile
from annotation.label_paths import working_label_rel_path
from annotation.models import AnnotationSubmission, AnnotationTask, ReviewRecord
from annotation.services import (
    approve_submission,
    reject_submission,
    submit_annotation,
    submit_inapp_annotation,
)
from annotation.visualization.slice_io import resolve_path
from core.choices import (
    LabelType,
    ReviewDecision,
    SubmissionReviewStatus,
    SubmissionSource,
    TaskStatus,
    TaskType,
    UserRole,
)
from projects.services import create_project
from volumes.models import Volume


User = get_user_model()
_ROOT = tempfile.mkdtemp(prefix="mito-dual-submit-")


def tiff_upload(name: str, values: np.ndarray) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    tifffile.imwrite(buffer, values)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/tiff")


requires_postgres = unittest.skipUnless(
    connection.vendor == "postgresql",
    "row locking is a no-op outside PostgreSQL",
)

DUAL_CHANNEL_SETTINGS = override_settings(
    MITO_DATA_ROOT=_ROOT, MEDIA_ROOT=_ROOT, FEATURE_REVIEW_HISTORY=True
)


class DualChannelFixtureMixin:
    """One task with a paintable working copy, ready for either channel."""

    def build(self):
        self.manager = User.objects.create_user("dual-manager", password="x")
        UserProfile.objects.filter(user=self.manager).update(role=UserRole.MANAGER)
        self.annotator = User.objects.create_user("dual-annotator", password="x")
        UserProfile.objects.filter(user=self.annotator).update(role=UserRole.ANNOTATOR)
        AnnotatorProfile.objects.create(user=self.annotator, is_active_annotator=True)
        self.project = create_project(title="Dual", created_by=self.manager, reviewed=True)
        image = resolve_path("images/dual.tif")
        image.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(image, np.zeros((3, 5, 5), dtype=np.uint8))
        self.volume = Volume.objects.create(
            project=self.project,
            name="dual-volume",
            image_path="images/dual.tif",
            label_type=LabelType.NONE,
            shape_z=3,
            shape_y=5,
            shape_x=5,
        )
        self.task = AnnotationTask.objects.create(
            project=self.project,
            volume=self.volume,
            assigned_to=self.annotator,
            z_start=0,
            z_end=3,
            y_end=5,
            x_end=5,
            task_type=TaskType.MANUAL_ANNOTATION,
            status=TaskStatus.ASSIGNED,
        )
        self.online_values = np.zeros((3, 5, 5), dtype=np.uint16)
        self.online_values[:, 1:3, 1:3] = 7
        working = resolve_path(working_label_rel_path(self.volume))
        working.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(working, self.online_values)
        self.offline_values = np.full((3, 5, 5), 11, dtype=np.uint16)

    def submit_both(self):
        offline = submit_annotation(
            task=self.task,
            annotator=self.annotator,
            label_file=tiff_upload("offline.tif", self.offline_values),
        )
        online = submit_inapp_annotation(task=self.task, annotator=self.annotator)
        return online, offline


@DUAL_CHANNEL_SETTINGS
class DualSubmissionChannelTests(DualChannelFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def test_resubmit_online_keeps_offline_pending_file(self):
        first_online, offline = self.submit_both()
        second_online = submit_inapp_annotation(task=self.task, annotator=self.annotator)

        offline.refresh_from_db()
        first_online.refresh_from_db()
        self.assertEqual(offline.review_status, SubmissionReviewStatus.PENDING)
        self.assertTrue(os.path.exists(offline.label_file.path))
        self.assertEqual(first_online.review_status, SubmissionReviewStatus.VOIDED)
        self.assertEqual(second_online.review_status, SubmissionReviewStatus.PENDING)
        self.assertEqual(
            AnnotationSubmission.objects.filter(
                task=self.task, review_status=SubmissionReviewStatus.PENDING
            ).count(),
            2,
        )

    def test_approve_online_installs_snapshot_and_voids_offline(self):
        online, offline = self.submit_both()
        # Prove approval uses the immutable online checkpoint, not later paint.
        tifffile.imwrite(resolve_path(working_label_rel_path(self.volume)), np.full((3, 5, 5), 99, dtype=np.uint16))

        approve_submission(online, reviewer=self.manager)

        self.volume.refresh_from_db()
        offline.refresh_from_db()
        self.assertTrue(np.array_equal(tifffile.imread(resolve_path(self.volume.label_location)), self.online_values))
        self.assertTrue(np.array_equal(tifffile.imread(resolve_path(working_label_rel_path(self.volume))), self.online_values))
        self.assertEqual(offline.review_status, SubmissionReviewStatus.VOIDED)
        self.assertIn("Online approve", offline.superseded_reason)
        review = ReviewRecord.objects.get(submission=online)
        self.assertEqual(review.source, "inapp")

    def test_approve_offline_installs_upload_and_voids_online(self):
        online, offline = self.submit_both()

        approve_submission(offline, reviewer=self.manager)

        self.volume.refresh_from_db()
        online.refresh_from_db()
        self.assertTrue(np.array_equal(tifffile.imread(resolve_path(self.volume.label_location)), self.offline_values))
        self.assertTrue(np.array_equal(tifffile.imread(resolve_path(working_label_rel_path(self.volume))), self.offline_values))
        self.assertEqual(online.review_status, SubmissionReviewStatus.VOIDED)
        self.assertIn("Offline approve", online.superseded_reason)
        self.assertEqual(self.task.reviews.get().source, "upload")

    def test_second_approve_loses_even_holding_a_stale_pending_handle(self):
        """Two managers racing Online vs Offline: only the first may install.

        The loser's handle still says ``pending`` in memory — exactly what a
        reviewer who opened the form before the other approve committed would
        hold. Approval must re-read under ``select_for_update`` and refuse, or
        both channels would each overwrite the official label in turn.
        """
        online, offline = self.submit_both()
        self.assertEqual(offline.review_status, SubmissionReviewStatus.PENDING)

        approve_submission(online, reviewer=self.manager)

        # `offline` is deliberately not refreshed: it is the stale snapshot.
        with self.assertRaises(ValueError):
            approve_submission(offline, reviewer=self.manager)

        self.volume.refresh_from_db()
        offline.refresh_from_db()
        self.assertTrue(np.array_equal(tifffile.imread(resolve_path(self.volume.label_location)), self.online_values))
        self.assertEqual(offline.review_status, SubmissionReviewStatus.VOIDED)
        self.assertEqual(
            ReviewRecord.objects.filter(task=self.task, decision="approved").count(), 1
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.last_decision_source, "inapp")

    def test_reject_online_leaves_offline_pending(self):
        online, offline = self.submit_both()

        reject_submission(online, reviewer=self.manager, comments="try another")

        online.refresh_from_db()
        offline.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(online.review_status, SubmissionReviewStatus.REJECTED)
        self.assertEqual(offline.review_status, SubmissionReviewStatus.PENDING)
        self.assertEqual(self.task.status, TaskStatus.SUBMITTED)
        self.assertEqual(online.reviews.get().source, "inapp")

    def test_reject_refuses_a_channel_the_other_approve_already_voided(self):
        """A reject form opened before the sibling channel won must not land.

        Otherwise the voided row collects a REJECTED record and the task's
        denormalized ``last_decision`` reads Rejected for work that was in
        fact approved through the other channel.
        """
        online, offline = self.submit_both()
        approve_submission(online, reviewer=self.manager)

        with self.assertRaises(ValueError):
            reject_submission(offline, reviewer=self.manager, comments="stale")

        offline.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(offline.review_status, SubmissionReviewStatus.VOIDED)
        self.assertEqual(offline.reviews.count(), 0)
        self.assertEqual(self.task.status, TaskStatus.APPROVED)
        self.assertEqual(self.task.last_decision, ReviewDecision.APPROVED)
        self.assertEqual(self.task.last_decision_source, SubmissionSource.INAPP)


# ---------------------------------------------------------------------------
# Concurrency — PostgreSQL only
# ---------------------------------------------------------------------------


@requires_postgres
@DUAL_CHANNEL_SETTINGS
class ConcurrentApproveTests(DualChannelFixtureMixin, TransactionTestCase):
    """Two managers approving opposite channels at the same instant.

    Approve installs the winner's file as the official label, so a lost race
    is not a harmless duplicate row — both channels would overwrite
    ``Volume.label_path`` in turn and the volume would end up with whichever
    copy finished last, with two 'approved' records claiming it. Exactly one
    approve may commit.
    """

    TIMEOUT = 30

    def setUp(self):
        self.build()

    def test_racing_approves_install_exactly_one_channel(self):
        online, offline = self.submit_both()
        errors, lock = [], threading.Lock()
        barrier = threading.Barrier(2, timeout=self.TIMEOUT)

        def worker(submission):
            try:
                barrier.wait()
                approve_submission(submission, reviewer=self.manager)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=worker, args=(submission,))
            for submission in (online, offline)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(self.TIMEOUT)
            self.assertFalse(thread.is_alive(), "approve deadlocked")

        # One loser, and it failed for the documented reason rather than a
        # database error that happened to abort it.
        self.assertEqual(len(errors), 1, f"expected one loser, got {errors}")
        self.assertIn("no longer pending", errors[0])

        approved = AnnotationSubmission.objects.filter(
            task=self.task, review_status=SubmissionReviewStatus.APPROVED
        )
        self.assertEqual(approved.count(), 1)
        self.assertEqual(
            AnnotationSubmission.objects.filter(
                task=self.task, review_status=SubmissionReviewStatus.VOIDED
            ).count(),
            1,
        )
        self.assertEqual(
            ReviewRecord.objects.filter(
                task=self.task, decision=ReviewDecision.APPROVED
            ).count(),
            1,
        )

        # The official label is the winner's bytes, not a blend of both writes.
        winner = approved.get()
        expected = (
            self.online_values if winner.source == SubmissionSource.INAPP
            else self.offline_values
        )
        self.volume.refresh_from_db()
        official = tifffile.imread(resolve_path(self.volume.label_location))
        self.assertTrue(np.array_equal(official, expected))
        self.assertTrue(np.array_equal(
            tifffile.imread(resolve_path(working_label_rel_path(self.volume))), expected
        ))
        self.task.refresh_from_db()
        self.assertEqual(self.task.last_decision_source, winner.source)
