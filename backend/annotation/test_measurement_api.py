import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from annotation.label_paths import working_label_rel_path
from annotation.measurement_jobs import run_measurement
from annotation.measurements import MeasurementError, measure_label_volume
from annotation.visualization import slice_io
from core.choices import LabelType
from processing.models import ProcessingJob
from processing.services import run_dispatch_once
from projects.models import Project
from volumes.models import Volume


class MeasurementAPITests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mito-measure-api-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        override = override_settings(MITO_DATA_ROOT=str(self.root))
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(slice_io.clear_caches)
        users = get_user_model()
        self.manager = users.objects.create_superuser("manager", password="test")
        self.owner = users.objects.create_user("owner")
        self.outsider = users.objects.create_user("outsider")
        project = Project.objects.create(title="Measure", created_by=self.owner)
        self.volume = Volume.objects.create(
            project=project, name="Tube", image_path="image.tif", label_path="labels.tif",
            label_type=LabelType.PARTIAL, shape_z=20, shape_y=20, shape_x=120,
            voxel_size_z=30, voxel_size_y=16, voxel_size_x=16,
        )
        labels = np.zeros((20, 20, 120), dtype=np.uint16)
        labels[7:13, 7:13, 10:110] = 5
        tifffile.imwrite(self.root / "labels.tif", labels)
        self.url = f"/api/volumes/{self.volume.pk}/measurements/"
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def queue(self, source="official"):
        response = self.client.post(self.url, {"source": source}, format="json")
        self.assertEqual(response.status_code, 202, response.data)
        return ProcessingJob.objects.get(pk=response.data["job"]["id"])

    def test_queue_dispatch_read_and_source_files_unchanged(self):
        before = hashlib.sha256((self.root / "labels.tif").read_bytes()).hexdigest()
        job = self.queue()
        self.assertEqual(job.status, "queued")
        self.assertEqual(run_dispatch_once(max_new=1, job_types=("measure_mito",))["submitted"], 1)
        response = self.client.get(self.url)
        data = response.data["job"]
        self.assertEqual(data["status"], "succeeded")
        self.assertTrue(data["is_current"])
        self.assertNotIn("path", data)
        self.assertEqual(data["result"]["voxel_size_nm_zyx"], [30, 16, 16])
        self.assertEqual(data["result"]["rows"][0]["label_id"], 5)
        self.assertEqual(data["result"]["rows"][0]["voxel_count"], 3600)
        self.assertEqual(before, hashlib.sha256((self.root / "labels.tif").read_bytes()).hexdigest())
        self.assertFalse((self.root / working_label_rel_path(self.volume)).exists())
        self.volume.refresh_from_db()
        self.assertEqual(self.volume.label_type, LabelType.PARTIAL)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_permissions_and_no_public_access(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(self.url, {"source": "official"}).status_code, 403)
        self.assertEqual(ProcessingJob.objects.count(), 0)

    def test_invalid_and_unknown_spacing_do_not_queue(self):
        self.assertEqual(self.client.post(self.url, {"source": "anything"}).status_code, 400)
        self.assertEqual(self.client.get(self.url + "?source=anything").status_code, 400)
        Volume.objects.filter(pk=self.volume.pk).update(voxel_size_z=None)
        response = self.client.post(self.url, {"source": "official"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("voxel size", response.data["detail"])
        self.assertEqual(ProcessingJob.objects.count(), 0)

    def test_duplicate_active_job_is_rejected(self):
        self.queue()
        self.assertEqual(self.client.post(self.url, {"source": "official"}).status_code, 409)
        self.assertEqual(ProcessingJob.objects.count(), 1)

    def test_working_never_falls_back_to_official(self):
        self.assertEqual(self.client.post(self.url, {"source": "working"}).status_code, 400)
        path = self.root / working_label_rel_path(self.volume)
        path.parent.mkdir(parents=True)
        labels = np.zeros((20, 20, 120), dtype=np.uint16)
        labels[7:13, 7:13, 10:110] = 19
        tifffile.imwrite(path, labels)
        self.queue("working")
        run_dispatch_once(max_new=1, job_types=("measure_mito",))
        result = self.client.get(self.url + "?source=working").data["job"]["result"]
        self.assertEqual(result["rows"][0]["label_id"], 19)
        self.assertIsNone(self.client.get(self.url).data["job"])

    def test_changed_input_before_run_fails_without_computing(self):
        job = self.queue()
        Volume.objects.filter(pk=self.volume.pk).update(voxel_size_x=32)
        with patch("annotation.measurement_jobs.measure_label_volume") as measure:
            outcome = run_measurement(job)
        self.assertEqual(outcome.status, "failed")
        measure.assert_not_called()

    def test_concurrent_label_change_discards_result(self):
        job = self.queue()
        def change(*args, **kwargs):
            path = self.root / "labels.tif"
            with path.open("ab") as handle:
                handle.write(b"change")
            return []
        with patch("annotation.measurement_jobs.measure_label_volume", side_effect=change):
            outcome = run_measurement(job)
        self.assertEqual(outcome.status, "failed")
        self.assertIn("discarded", outcome.error_message)
        self.assertEqual(outcome.output_paths, {})

    def test_old_results_remain_explicitly_historical(self):
        self.queue()
        run_dispatch_once(max_new=1, job_types=("measure_mito",))
        Volume.objects.filter(pk=self.volume.pk).update(voxel_size_x=32)
        data = self.client.get(self.url).data["job"]
        self.assertFalse(data["is_current"])
        self.assertEqual(data["result"]["voxel_size_nm_zyx"], [30, 16, 16])

    def test_crop_limit_fails_instead_of_reporting_partial_measurements(self):
        with self.assertRaisesMessage(MeasurementError, "web measurement limit"):
            measure_label_volume(self.root / "labels.tif", [30, 16, 16], max_crop_voxels=1)

    def test_missing_result_is_explicit(self):
        job = self.queue()
        ProcessingJob.objects.filter(pk=job.pk).update(status="succeeded", output_paths={})
        data = self.client.get(self.url).data["job"]
        self.assertIsNone(data["result"])
        self.assertIn("unavailable", data["error"])

    def test_explicit_spacing_update_enables_measurement_without_label_writes(self):
        Volume.objects.filter(pk=self.volume.pk).update(voxel_size_z=None, voxel_size_y=None, voxel_size_x=None)
        before = (self.root / "labels.tif").read_bytes()
        response = self.client.patch(f"/api/volumes/{self.volume.pk}/", {
            "voxel_size_z": 30, "voxel_size_y": 16, "voxel_size_x": 16,
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.queue()
        self.assertEqual(before, (self.root / "labels.tif").read_bytes())
