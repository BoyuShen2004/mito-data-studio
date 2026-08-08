import tempfile
from pathlib import Path

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from core.choices import ProcessingJobType, UserRole
from processing.models import ProcessingJob
from projects.models import Dataset, Project
from volumes.models import Volume
from volumes.services import register_volume


class PyramidProductPathTests(APITestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mito-pyramid-api-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "data"
        self.root.mkdir()
        self.source = base / "source.tif"
        tifffile.imwrite(self.source, np.zeros((4, 32, 32), dtype=np.uint16))
        User = get_user_model()
        self.manager = User.objects.create_user("pyramid-manager")
        self.manager.profile.role = UserRole.MANAGER
        self.manager.profile.save(update_fields=["role"])
        self.requester = User.objects.create_user("pyramid-requester")
        self.requester.profile.role = UserRole.REQUESTER
        self.requester.profile.save(update_fields=["role"])
        self.project = Project.objects.create(
            title="Streaming", created_by=self.requester
        )
        self.dataset = Dataset.objects.create(project=self.project, name="Data")

    def create_volume(self):
        return Volume.objects.create(
            project=self.project,
            dataset=self.dataset,
            name="source",
            image_path=str(self.source),
        )

    @override_settings(FEATURE_VOLUME_PYRAMIDS=True)
    def test_registration_auto_enqueues_without_running_the_build_inline(self):
        with override_settings(MITO_DATA_ROOT=self.root):
            volume = register_volume(
                project=self.project,
                dataset=self.dataset,
                name="registered",
                image_path=str(self.source),
                created_by=self.requester,
            )
        job = ProcessingJob.objects.get(
            volume=volume, job_type=ProcessingJobType.BUILD_PYRAMID
        )
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.config["trigger"], "registration")
        volume.refresh_from_db()
        self.assertFalse(volume.ready_streaming)

    @override_settings(FEATURE_VOLUME_PYRAMIDS=False)
    def test_registration_flag_off_creates_no_build_job(self):
        with override_settings(MITO_DATA_ROOT=self.root):
            volume = register_volume(
                project=self.project,
                dataset=self.dataset,
                name="fallback",
                image_path=str(self.source),
            )
        self.assertFalse(volume.processing_jobs.exists())

    @override_settings(FEATURE_VOLUME_PYRAMIDS=True)
    def test_manager_can_queue_and_volume_payload_reports_building(self):
        volume = self.create_volume()
        self.client.force_authenticate(self.manager)
        response = self.client.post(f"/api/volumes/{volume.pk}/pyramid/", {})
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["volume"]["streaming_status"], "building")
        self.assertEqual(response.data["volume"]["pyramid_job_id"], response.data["job_id"])

    @override_settings(FEATURE_VOLUME_PYRAMIDS=True)
    def test_requester_cannot_trigger_a_build(self):
        volume = self.create_volume()
        self.client.force_authenticate(self.requester)
        response = self.client.post(f"/api/volumes/{volume.pk}/pyramid/", {})
        self.assertEqual(response.status_code, 403)

    @override_settings(FEATURE_VOLUME_PYRAMIDS=False)
    def test_disabled_trigger_returns_a_typed_503(self):
        volume = self.create_volume()
        self.client.force_authenticate(self.manager)
        response = self.client.post(f"/api/volumes/{volume.pk}/pyramid/", {})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["reason"], "disabled")
