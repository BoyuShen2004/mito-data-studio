"""Phase 12 — the pyramid build as a submittable job.

Doc 20 §Pyramid job names the flow; Phase 11 built the middle of it and this
covers submission, execution, duplicate refusal, idempotent replay, failure
recording and cancellation.

**No real scheduler is contacted.** Slurm routing is exercised only through the
existing backend registry with a fake adapter — HPC/Slurm integration proper is
phase map row 18, not row 12.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.choices import ProcessingJobStatus, ProcessingJobType
from processing.models import ProcessingJob
from projects.models import Dataset, Project
from volumes.models import Volume
from volumes.pyramid import jobs, store

User = get_user_model()

ON = dict(FEATURE_VOLUME_PYRAMIDS=True)
# The disabled case, stated explicitly. These used to override only
# MITO_DATA_ROOT and let FEATURE_VOLUME_PYRAMIDS fall through to the
# settings default — off in the `legacy` profile they were written under,
# but on in the deployed `production_integrated_v1`, where pyramids are
# the streaming read path. They therefore asserted disabled behaviour of
# an enabled service and failed under the live profile.
OFF = dict(FEATURE_VOLUME_PYRAMIDS=False)
SHAPE = (4, 128, 128)


def _zarr_available() -> bool:
    try:
        store.require_zarr()
        return True
    except Exception:  # pragma: no cover
        return False


class PyramidJobTestCase(TestCase):
    def setUp(self):
        if not _zarr_available():  # pragma: no cover
            self.skipTest("zarr is an optional dependency and is not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir(parents=True)
        external = Path(self.tmp.name) / "src"
        external.mkdir()

        rng = np.random.default_rng(53)
        self.image = external / "cortex.tif"
        tifffile.imwrite(
            str(self.image), rng.integers(0, 3000, size=SHAPE, dtype=np.uint16)
        )
        self.image_bytes = self.image.read_bytes()

        self.user = User.objects.create_user(username="builder", password="x")
        self.project = Project.objects.create(title="Proj", created_by=self.user)
        self.dataset = Dataset.objects.create(project=self.project, name="DS")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="cortex",
            image_path=str(self.image),
        )

    def spec(self, **kw):
        return jobs.PyramidBuildSpec(volume_id=self.volume.pk, **kw)


class Submission(PyramidJobTestCase):
    def test_disabled_refuses_to_submit(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **OFF):
            with self.assertRaises(jobs.PyramidJobError) as ctx:
                jobs.submit_build(self.spec())
        self.assertEqual(ctx.exception.reason, "disabled")
        self.assertEqual(ProcessingJob.objects.count(), 0)

    def test_a_submitted_job_is_queued_and_linked_to_its_volume(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            job = jobs.submit_build(self.spec(), actor=self.user)
        self.assertEqual(job.status, ProcessingJobStatus.QUEUED)
        self.assertEqual(job.job_type, ProcessingJobType.BUILD_PYRAMID)
        self.assertEqual(job.volume_id, self.volume.pk)
        self.assertEqual(job.project_id, self.project.pk)
        self.assertEqual(job.created_by_id, self.user.pk)

    def test_a_duplicate_active_build_is_refused(self):
        """Two builds writing one derivative would interleave."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            first = jobs.submit_build(self.spec())
            with self.assertRaises(jobs.DuplicateBuild) as ctx:
                jobs.submit_build(self.spec())
        self.assertEqual(ctx.exception.job_id, first.pk)
        self.assertEqual(ProcessingJob.objects.count(), 1)

    def test_an_idempotency_key_replays_rather_than_duplicating(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            first = jobs.submit_build(self.spec(idempotency_key="k-1"))
            replay = jobs.submit_build(self.spec(idempotency_key="k-1"))
        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(ProcessingJob.objects.count(), 1)

    def test_a_claimed_build_is_still_a_duplicate(self):
        """SUBMITTED is where a scheduled job spends most of its life."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            first = jobs.submit_build(self.spec())
            ProcessingJob.objects.filter(pk=first.pk).update(
                status=ProcessingJobStatus.SUBMITTED
            )
            with self.assertRaises(jobs.DuplicateBuild):
                jobs.submit_build(self.spec())
        self.assertEqual(ProcessingJob.objects.count(), 1)

    def test_a_key_replays_only_while_its_build_is_unfinished(self):
        """Otherwise a rebuild of the same layer can never be requested again."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            first = jobs.submit_build(self.spec(idempotency_key="k-2"))
            ProcessingJob.objects.filter(pk=first.pk).update(
                status=ProcessingJobStatus.SUCCEEDED
            )
            second = jobs.submit_build(self.spec(idempotency_key="k-2"))
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.status, ProcessingJobStatus.QUEUED)

    def test_a_finished_build_does_not_block_a_new_one(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            first = jobs.submit_build(self.spec())
            ProcessingJob.objects.filter(pk=first.pk).update(
                status=ProcessingJobStatus.SUCCEEDED
            )
            second = jobs.submit_build(self.spec())
        self.assertNotEqual(first.pk, second.pk)

    def test_the_spec_carries_no_scheduler_concepts(self):
        """A spec mentioning partitions would make the algorithm need Slurm."""
        payload = self.spec().as_payload()
        for banned in ("partition", "sbatch", "nodes", "cpus", "command"):
            self.assertNotIn(banned, payload)


class Execution(PyramidJobTestCase):
    def test_a_successful_run_builds_and_marks_the_volume_ready(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            job = jobs.submit_build(self.spec())
            outcome = jobs.run_build(job)
            job.refresh_from_db()
        self.volume.refresh_from_db()
        self.assertEqual(job.status, ProcessingJobStatus.SUCCEEDED)
        self.assertTrue(self.volume.ready_streaming)
        self.assertTrue(outcome["validated"])
        self.assertIn("pyramid", job.output_paths)

    def test_region_build_calculates_coverage_outside_registration(self):
        mask = np.zeros(SHAPE, dtype=np.uint8)
        mask[:, :, :32] = 1
        region = self.image.parent / "region.tif"
        tifffile.imwrite(str(region), mask)
        self.volume.region_mask_path = str(region)
        self.volume.save(update_fields=["region_mask_path"])

        report = SimpleNamespace(
            layer="region",
            path="pyramids/region.zarr",
            as_dict=lambda: {"layer": "region", "validated": True},
        )
        with (
            override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON),
            patch.object(jobs.pyramid_service, "build_pyramid", return_value=report),
        ):
            job = jobs.submit_build(self.spec(layer="region"))
            jobs.run_build(job)

        self.volume.refresh_from_db()
        self.assertAlmostEqual(self.volume.region_mask_coverage, 0.25)

    def test_a_failing_run_records_the_failure_and_promotes_nothing(self):
        self.volume.image_path = str(self.image.parent / "missing.tif")
        self.volume.save(update_fields=["image_path"])
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            job = jobs.submit_build(self.spec())
            with self.assertRaises(Exception):
                jobs.run_build(job)
            job.refresh_from_db()
            location = store.pyramid_location(self.volume)
        self.assertEqual(job.status, ProcessingJobStatus.FAILED)
        self.assertTrue(job.error_message)
        self.assertFalse(location.path.exists())
        self.assertFalse(location.tmp_path.exists())
        self.volume.refresh_from_db()
        self.assertFalse(self.volume.ready_streaming)

    def test_a_run_never_modifies_the_source_image(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            job = jobs.submit_build(self.spec())
            jobs.run_build(job)
        self.assertEqual(self.image.read_bytes(), self.image_bytes)

    def test_a_rebuild_invalidates_the_chunk_handle_cache(self):
        from volumes.chunks import core as chunk_core

        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            jobs.run_build(jobs.submit_build(self.spec()))
            chunk_core.HANDLES.get((self.volume.pk, "stale"), lambda: object())
            before = chunk_core.HANDLES.size()
            jobs.run_build(jobs.submit_build(self.spec()))
            after = chunk_core.HANDLES.size()
        self.assertLess(after, before + 1)

    def test_the_processing_dispatcher_executes_the_real_pyramid_runner(self):
        from processing.services import run_dispatch_once

        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            job = jobs.submit_build(self.spec())
            summary = run_dispatch_once(
                max_new=1,
                poll_active=False,
                job_types=(ProcessingJobType.BUILD_PYRAMID,),
            )
        job.refresh_from_db()
        self.volume.refresh_from_db()
        self.assertEqual(summary["submitted"], 1)
        self.assertEqual(job.status, ProcessingJobStatus.SUCCEEDED)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertTrue(self.volume.ready_streaming)


class Cancellation(PyramidJobTestCase):
    def test_a_queued_build_can_be_cancelled(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            job = jobs.submit_build(self.spec())
            self.assertTrue(jobs.cancel_build(job))
        self.assertEqual(job.status, ProcessingJobStatus.CANCELLED)

    def test_a_finished_build_cannot_be_cancelled(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            job = jobs.submit_build(self.spec())
            jobs.run_build(job)
            self.assertFalse(jobs.cancel_build(job))

    def test_a_cancelled_build_frees_the_volume_for_a_new_submission(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            first = jobs.submit_build(self.spec())
            jobs.cancel_build(first)
            second = jobs.submit_build(self.spec())
        self.assertNotEqual(first.pk, second.pk)


class StatusReporting(PyramidJobTestCase):
    def test_status_reports_state_without_leaking_a_path(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            job = jobs.submit_build(self.spec())
            status = jobs.job_status(job)
        self.assertEqual(status["status"], ProcessingJobStatus.QUEUED)
        self.assertEqual(status["volume_id"], self.volume.pk)
        blob = str(status)
        self.assertNotIn(str(self.root), blob)
        self.assertNotIn(".zarr", blob)


class BackendRouting(PyramidJobTestCase):
    """The runner boundary. No real scheduler is contacted — row 18 owns that."""

    def test_the_backend_registry_resolves_local_and_slurm_without_submitting(self):
        from processing.registry import get_processing_backend

        local = get_processing_backend("local")
        slurm = get_processing_backend("slurm")
        self.assertEqual(local.name, "local")
        self.assertTrue(hasattr(slurm, "submit"))
        self.assertTrue(hasattr(slurm, "poll"))
        self.assertTrue(hasattr(slurm, "cancel"))

    def test_an_unknown_backend_is_refused(self):
        from processing.registry import get_processing_backend

        with self.assertRaises(ValueError):
            get_processing_backend("definitely-not-a-backend")

    def test_the_job_records_which_backend_it_was_submitted_for(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            job = jobs.submit_build(self.spec(), backend_name="slurm")
        self.assertEqual(job.backend, "slurm")
        # Recorded only — nothing was submitted anywhere.
        self.assertEqual(job.status, ProcessingJobStatus.QUEUED)
        self.assertEqual(job.external_job_id, "")
