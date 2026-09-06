"""Gold-standard and reviewer-agreement scoring, end to end.

Covers the two decisions that make this module honest:

* an unscoreable comparison writes **nothing**, rather than a row of zeros;
* an annotator with no measured submissions reports ``None``, not ``0.0``.
"""

from __future__ import annotations

import tempfile

import numpy as np
import tifffile
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import AnnotatorProfile, UserProfile
from annotation.models import AnnotationSubmission, AnnotationTask, QualityScore
from annotation.quality_scoring import (
    annotator_quality,
    refresh_annotator_quality,
    score_against_gold_standard,
    score_submission,
)
from core.choices import QualityScoreKind, TaskType, UserRole
from projects.models import Dataset, Project
from volumes.models import Volume

_TMP = tempfile.mkdtemp(prefix="mito_quality_")
ENABLED = override_settings(FEATURE_QUALITY_METRICS=True, MITO_DATA_ROOT=_TMP)


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


def label_file(name, array):
    import io

    buffer = io.BytesIO()
    tifffile.imwrite(buffer, np.asarray(array, dtype=np.uint16))
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/tiff")


@ENABLED
class QualityScoringTests(TestCase):
    def setUp(self):
        self.manager = make_user("mgr", UserRole.MANAGER)
        self.annotator = make_user("ann", UserRole.ANNOTATOR)
        self.project = Project.objects.create(title="P", created_by=self.manager)
        self.dataset = Dataset.objects.create(project=self.project, name="D")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="V",
            shape_z=1, shape_y=2, shape_x=4,
        )
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume, assigned_to=self.annotator,
            z_start=0, z_end=1, y_end=2, x_end=4,
            task_type=TaskType.MANUAL_ANNOTATION,
        )

    def submission(self, array, annotator=None, name="s.tif"):
        return AnnotationSubmission.objects.create(
            task=self.task,
            annotator=annotator or self.annotator,
            label_file=label_file(name, array),
        )

    # --- scoring ----------------------------------------------------------

    def test_a_perfect_match_scores_one(self):
        truth = [[1, 1, 0, 0], [0, 0, 2, 2]]
        reference = self.submission(truth, self.manager, "ref.tif")
        candidate = self.submission(truth, self.annotator, "cand.tif")
        score = score_submission(
            candidate, reference, kind=QualityScoreKind.GOLD_STANDARD
        )
        self.assertEqual(score.dice, 1.0)
        self.assertEqual(score.instance_f1, 1.0)
        self.assertEqual(score.false_merges, 0)

    def test_a_merge_is_recorded_even_at_perfect_dice(self):
        reference = self.submission([[1, 1, 2, 2], [0, 0, 0, 0]], self.manager, "r.tif")
        candidate = self.submission([[1, 1, 1, 1], [0, 0, 0, 0]], self.annotator, "c.tif")
        score = score_submission(
            candidate, reference, kind=QualityScoreKind.GOLD_STANDARD
        )
        self.assertEqual(score.dice, 1.0)
        self.assertEqual(score.false_merges, 1)

    def test_a_shape_mismatch_writes_nothing(self):
        """Recording it as 0.0 Dice would libel the annotator for what is
        really a configuration error."""
        reference = self.submission([[1, 1, 0, 0], [0, 0, 0, 0]], self.manager, "r.tif")
        candidate = self.submission([[1, 1, 1]], self.annotator, "c.tif")
        self.assertIsNone(
            score_submission(candidate, reference, kind=QualityScoreKind.GOLD_STANDARD)
        )
        self.assertEqual(QualityScore.objects.count(), 0)

    def test_a_submission_is_never_scored_against_itself(self):
        only = self.submission([[1, 1, 0, 0], [0, 0, 0, 0]])
        self.assertIsNone(
            score_submission(only, only, kind=QualityScoreKind.GOLD_STANDARD)
        )

    def test_a_missing_reference_writes_nothing(self):
        candidate = self.submission([[1, 1, 0, 0], [0, 0, 0, 0]])
        self.assertIsNone(
            score_submission(candidate, None, kind=QualityScoreKind.GOLD_STANDARD)
        )

    # --- gold standard gating ---------------------------------------------

    def test_an_ordinary_volume_is_not_scored(self):
        candidate = self.submission([[1, 1, 0, 0], [0, 0, 0, 0]])
        self.assertIsNone(score_against_gold_standard(candidate))
        self.assertEqual(QualityScore.objects.count(), 0)

    def test_a_gold_standard_volume_is_scored_on_submit(self):
        truth = [[1, 1, 0, 0], [0, 0, 0, 0]]
        reference = self.submission(truth, self.manager, "ref.tif")
        self.volume.is_gold_standard = True
        self.volume.reference_submission = reference
        self.volume.save(update_fields=["is_gold_standard", "reference_submission"])

        candidate = self.submission(truth, self.annotator, "cand.tif")
        score = score_against_gold_standard(candidate)
        self.assertIsNotNone(score)
        self.assertEqual(score.kind, QualityScoreKind.GOLD_STANDARD)

    def test_a_gold_standard_flag_without_a_reference_scores_nothing(self):
        self.volume.is_gold_standard = True
        self.volume.save(update_fields=["is_gold_standard"])
        candidate = self.submission([[1, 1, 0, 0], [0, 0, 0, 0]])
        self.assertIsNone(score_against_gold_standard(candidate))

    def test_scoring_is_inert_while_the_flag_is_off(self):
        truth = [[1, 1, 0, 0], [0, 0, 0, 0]]
        reference = self.submission(truth, self.manager, "ref.tif")
        self.volume.is_gold_standard = True
        self.volume.reference_submission = reference
        self.volume.save(update_fields=["is_gold_standard", "reference_submission"])
        candidate = self.submission(truth, self.annotator, "cand.tif")
        with override_settings(FEATURE_QUALITY_METRICS=False):
            self.assertIsNone(score_against_gold_standard(candidate))

    # --- rolling annotator score ------------------------------------------

    def test_an_unmeasured_annotator_reports_none_not_zero(self):
        """The distinction the whole quality module is built on."""
        self.assertIsNone(annotator_quality(self.annotator))
        self.assertIsNone(refresh_annotator_quality(self.annotator))

    def test_the_rolling_score_averages_gold_standard_dice(self):
        truth = [[1, 1, 0, 0], [0, 0, 0, 0]]
        reference = self.submission(truth, self.manager, "ref.tif")
        # One perfect, one half-right.
        score_submission(
            self.submission(truth, self.annotator, "a.tif"), reference,
            kind=QualityScoreKind.GOLD_STANDARD,
        )
        score_submission(
            self.submission([[1, 0, 0, 0], [0, 0, 0, 0]], self.annotator, "b.tif"),
            reference, kind=QualityScoreKind.GOLD_STANDARD,
        )
        rolling = annotator_quality(self.annotator)
        self.assertIsNotNone(rolling)
        self.assertLess(rolling, 1.0)
        self.assertGreater(rolling, 0.0)

    def test_reviewer_agreement_scores_do_not_move_the_rolling_number(self):
        """They measure how much a reviewer chose to change, which would mix
        the reviewer's standards into a figure describing the annotator."""
        truth = [[1, 1, 0, 0], [0, 0, 0, 0]]
        reference = self.submission(truth, self.manager, "ref.tif")
        score_submission(
            self.submission([[1, 0, 0, 0], [0, 0, 0, 0]], self.annotator, "c.tif"),
            reference, kind=QualityScoreKind.REVIEWER_AGREEMENT,
        )
        self.assertIsNone(annotator_quality(self.annotator))

    def test_the_window_bounds_how_many_scores_count(self):
        truth = [[1, 1, 0, 0], [0, 0, 0, 0]]
        reference = self.submission(truth, self.manager, "ref.tif")
        for index in range(4):
            score_submission(
                self.submission(truth, self.annotator, f"p{index}.tif"), reference,
                kind=QualityScoreKind.GOLD_STANDARD,
            )
        with override_settings(MITO_QUALITY_SCORE_WINDOW=2):
            self.assertEqual(annotator_quality(self.annotator), 1.0)


@ENABLED
class ApprovedSubmissionPickerTests(TestCase):
    """``GET /api/volumes/<pk>/approved-submissions/`` — reference candidates."""

    def setUp(self):
        from rest_framework.test import APIClient

        self.manager = make_user("mgr2", UserRole.MANAGER)
        self.annotator = make_user("ann2", UserRole.ANNOTATOR)
        self.project = Project.objects.create(title="P", created_by=self.manager)
        self.dataset = Dataset.objects.create(project=self.project, name="D")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="V",
            shape_z=1, shape_y=2, shape_x=4,
        )
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume, assigned_to=self.annotator,
            z_start=0, z_end=1, y_end=2, x_end=4,
            task_type=TaskType.MANUAL_ANNOTATION,
        )
        self.client = APIClient()

    def make_submission(self, review_status, name="s.tif"):
        return AnnotationSubmission.objects.create(
            task=self.task, annotator=self.annotator,
            label_file=label_file(name, [[1, 1, 0, 0], [0, 0, 0, 0]]),
            review_status=review_status,
        )

    def test_only_approved_submissions_are_offered(self):
        """A pending row is the review queue, not a trusted answer."""
        from core.choices import SubmissionReviewStatus

        approved = self.make_submission(SubmissionReviewStatus.APPROVED, "a.tif")
        self.make_submission(SubmissionReviewStatus.PENDING, "b.tif")
        self.make_submission(SubmissionReviewStatus.REJECTED, "c.tif")

        self.client.force_authenticate(self.manager)
        response = self.client.get(
            f"/api/volumes/{self.volume.pk}/approved-submissions/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["id"] for row in response.data["results"]], [approved.pk]
        )

    def test_every_approved_round_qualifies_not_only_the_latest(self):
        """Unlike the review queue, which keeps one row per task and channel."""
        from core.choices import SubmissionReviewStatus

        first = self.make_submission(SubmissionReviewStatus.APPROVED, "a.tif")
        second = self.make_submission(SubmissionReviewStatus.APPROVED, "b.tif")
        self.client.force_authenticate(self.manager)
        response = self.client.get(
            f"/api/volumes/{self.volume.pk}/approved-submissions/"
        )
        self.assertEqual(
            {row["id"] for row in response.data["results"]}, {first.pk, second.pk}
        )

    def test_an_annotator_may_not_enumerate_them(self):
        self.client.force_authenticate(self.annotator)
        response = self.client.get(
            f"/api/volumes/{self.volume.pk}/approved-submissions/"
        )
        self.assertEqual(response.status_code, 403)

    def test_disabled_reports_503(self):
        self.client.force_authenticate(self.manager)
        with override_settings(FEATURE_QUALITY_METRICS=False):
            response = self.client.get(
                f"/api/volumes/{self.volume.pk}/approved-submissions/"
            )
        self.assertEqual(response.status_code, 503)

    def test_a_reference_from_another_volume_is_refused(self):
        """Two unrelated rasters that happen to share a shape would produce a
        confidently wrong score."""
        from core.choices import SubmissionReviewStatus

        other_volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="Other",
            shape_z=1, shape_y=2, shape_x=4,
        )
        other_task = AnnotationTask.objects.create(
            project=self.project, volume=other_volume,
            z_start=0, z_end=1, y_end=2, x_end=4,
            task_type=TaskType.MANUAL_ANNOTATION,
        )
        foreign = AnnotationSubmission.objects.create(
            task=other_task, annotator=self.annotator,
            label_file=label_file("f.tif", [[1, 0, 0, 0], [0, 0, 0, 0]]),
            review_status=SubmissionReviewStatus.APPROVED,
        )
        self.client.force_authenticate(self.manager)
        response = self.client.put(
            f"/api/volumes/{self.volume.pk}/gold-standard/",
            {"is_gold_standard": True, "reference_submission": foreign.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_enabling_without_a_reference_is_refused(self):
        self.client.force_authenticate(self.manager)
        response = self.client.put(
            f"/api/volumes/{self.volume.pk}/gold-standard/",
            {"is_gold_standard": True}, format="json",
        )
        self.assertEqual(response.status_code, 400)
