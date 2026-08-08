"""Tests for the ProcessingJob foundation: creation, local adapter, dispatch."""

import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.choices import ProcessingJobStatus, ProcessingJobType
from processing.models import ProcessingJob
from processing.registry import get_processing_backend
from processing.adapters.slurm import SlurmProcessingBackend
from processing.services import (
    claim_next_queued_job,
    create_processing_job,
    dispatch_job,
    retry_job,
    run_dispatch_once,
)

_TMP_ROOT = tempfile.mkdtemp(prefix="mito-processing-test-")

User = get_user_model()


@override_settings(
    MITO_SHARED_STORAGE_ROOT=_TMP_ROOT, MITO_PROCESSING_BACKEND="local"
)
class ProcessingServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("mgr", password="x")

    def test_create_job_is_queued(self):
        job = create_processing_job(
            job_type=ProcessingJobType.INGEST, created_by=self.user
        )
        self.assertEqual(job.status, ProcessingJobStatus.QUEUED)
        self.assertEqual(job.backend, "local")

    def test_unknown_job_type_rejected(self):
        with self.assertRaises(ValueError):
            create_processing_job(job_type="not_a_type")

    def test_provider_selection(self):
        self.assertEqual(get_processing_backend().name, "local")
        self.assertEqual(get_processing_backend("slurm").name, "slurm")
        with self.assertRaises(ValueError):
            get_processing_backend("nope")

    def test_slurm_builds_private_script_from_argv(self):
        job = create_processing_job(
            job_type=ProcessingJobType.PREDICT,
            backend="slurm",
            config={"argv": ["nnUNetv2_predict", "-i", "path with spaces"]},
        )
        script = Path(SlurmProcessingBackend._resolve_script(job))
        self.assertEqual(script.stat().st_mode & 0o777, 0o700)
        self.assertIn("'path with spaces'", script.read_text())

    def test_local_dispatch_succeeds(self):
        job = create_processing_job(job_type=ProcessingJobType.PREDICT)
        claimed = claim_next_queued_job()
        self.assertEqual(claimed.id, job.id)
        self.assertEqual(claimed.status, ProcessingJobStatus.SUBMITTED)
        dispatch_job(claimed)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, ProcessingJobStatus.SUCCEEDED)
        self.assertTrue(claimed.external_job_id)
        self.assertIn("result", claimed.output_paths)
        self.assertIsNotNone(claimed.finished_at)

    @override_settings(MITO_LOCAL_EXECUTABLE_ALLOWLIST="true")
    def test_local_real_command_writes_versioned_manifest_without_a_shell(self):
        job = create_processing_job(
            job_type=ProcessingJobType.PREDICT,
            config={"argv": ["/bin/true"]},
        )
        dispatch_job(claim_next_queued_job())
        job.refresh_from_db()
        self.assertEqual(job.status, ProcessingJobStatus.SUCCEEDED, job.error_message)
        manifest = json.loads(Path(job.output_paths["result"]).read_text())
        self.assertEqual(manifest["argv"], ["/bin/true"])
        self.assertEqual(manifest["schema"], 1)

    @override_settings(MITO_LOCAL_EXECUTABLE_ALLOWLIST="nnUNetv2_predict")
    def test_local_real_command_rejects_an_unapproved_executable(self):
        job = create_processing_job(
            job_type=ProcessingJobType.PREDICT,
            config={"argv": ["/bin/true"]},
        )
        dispatch_job(claim_next_queued_job())
        job.refresh_from_db()
        self.assertEqual(job.status, ProcessingJobStatus.FAILED)
        self.assertIn("not allow-listed", job.error_message)

    def test_run_dispatch_once(self):
        for _ in range(3):
            create_processing_job(job_type=ProcessingJobType.INSPECT)
        summary = run_dispatch_once()
        self.assertEqual(summary["submitted"], 3)
        self.assertEqual(
            ProcessingJob.objects.filter(
                status=ProcessingJobStatus.SUCCEEDED
            ).count(),
            3,
        )

    def test_claim_returns_none_when_empty(self):
        self.assertIsNone(claim_next_queued_job())

    def test_retry_requeues_terminal_job(self):
        job = create_processing_job(job_type=ProcessingJobType.INGEST)
        job.status = ProcessingJobStatus.FAILED
        job.error_message = "boom"
        job.save(update_fields=["status", "error_message"])
        retry_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, ProcessingJobStatus.QUEUED)
        self.assertEqual(job.retry_count, 1)
        self.assertEqual(job.error_message, "")

    def test_retry_rejects_non_terminal(self):
        job = create_processing_job(job_type=ProcessingJobType.INGEST)
        with self.assertRaises(ValueError):
            retry_job(job)
