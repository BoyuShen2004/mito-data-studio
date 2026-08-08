"""Tests for the modular providers (QC, visualization, publishing)."""

import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from annotation.models import AnnotationSubmission, AnnotationTask
from annotation.publishing.registry import get_publishing_provider
from annotation.quality_control.registry import get_qc_provider
from annotation.services import get_visualization_state
from annotation.visualization.registry import get_visualization_provider
from core.choices import LabelType, TaskType
from projects.services import create_project
from volumes.models import Volume

User = get_user_model()
_TMP_ROOT = tempfile.mkdtemp(prefix="mito-providers-test-")


@override_settings(MITO_DATA_ROOT=_TMP_ROOT, MEDIA_ROOT=_TMP_ROOT)
class ProviderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("u", password="x")
        self.project = create_project(title="P", created_by=self.user, reviewed=True)
        self.volume = Volume.objects.create(
            project=self.project,
            name="v",
            image_path="images/v.tif",
            label_path="labels/v.tif",
            label_type=LabelType.PREDICTION,
            shape_z=8,
            shape_y=16,
            shape_x=16,
        )
        self.task = AnnotationTask.objects.create(
            project=self.project,
            volume=self.volume,
            z_start=0,
            z_end=8,
            y_end=16,
            x_end=16,
            task_type=TaskType.PREDICTION_PROOFREADING,
        )

    # --- QC ----------------------------------------------------------------
    def test_qc_report_shape(self):
        upload = SimpleUploadedFile("label.tif", b"II*\x00data")
        submission = AnnotationSubmission.objects.create(
            task=self.task, annotator=self.user, label_file=upload
        )
        report = get_qc_provider().validate_submission(submission)
        for key in ("passed", "checks", "metrics", "warnings", "errors"):
            self.assertIn(key, report)
        self.assertTrue(report["passed"])

    def test_qc_bad_extension_fails(self):
        upload = SimpleUploadedFile("notes.txt", b"nope")
        submission = AnnotationSubmission.objects.create(
            task=self.task, annotator=self.user, label_file=upload
        )
        report = get_qc_provider().validate_submission(submission)
        self.assertFalse(report["passed"])
        self.assertTrue(report["errors"])

    # --- visualization -----------------------------------------------------
    @override_settings(MITO_VISUALIZATION_PROVIDER="placeholder")
    def test_placeholder_visualization_unavailable(self):
        state = get_visualization_state(self.task)
        self.assertFalse(state["available"])
        self.assertEqual(state["url"], "")

    def test_inapp_visualization_gives_viewer_url(self):
        state = get_visualization_state(self.task)
        self.assertEqual(state["provider"], "inapp")
        self.assertEqual(state["url"], f"/viewer/tasks/{self.task.id}")
        self.assertEqual(state["mode"], "slice_viewer")

    @override_settings(
        MITO_VISUALIZATION_PROVIDER="neuroglancer",
        MITO_NEUROGLANCER_BASE_URL="https://ng.example/#",
    )
    def test_neuroglancer_visualization_available(self):
        state = get_visualization_state(self.task)
        self.assertTrue(state["available"])
        self.assertIn("image=", state["url"])

    # --- publishing --------------------------------------------------------
    def test_placeholder_publishing_records_intent(self):
        result = get_publishing_provider().publish_result(self.project)
        self.assertFalse(result["published"])
        self.assertEqual(result["target"], self.project.title)

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            get_qc_provider("nope")
        with self.assertRaises(ValueError):
            get_visualization_provider("nope")
