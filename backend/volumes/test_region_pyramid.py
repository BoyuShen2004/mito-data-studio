"""The region mask as a second read-only streaming layer (ADR-009 addendum).

The image pyramid already had coverage; what is new here is that a volume now
carries **two independent derivatives**. So these tests are mostly about
independence — building one layer must not flip, clear, or overwrite the other —
plus the property that makes a region pyramid correct at all: it is reduced by
**mode**, not mean, so a coarse mag still says "inside" or "outside" rather than
some average of the two.

Everything runs against an isolated temporary ``MITO_DATA_ROOT``; sources live
in a separate tempdir and are asserted byte-identical afterwards.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.choices import ProcessingJobStatus, ProcessingJobType
from processing.models import ProcessingJob
from projects.models import Dataset, Project
from volumes.models import Volume
from volumes.pyramid import jobs, service, store
from volumes.serializers import VolumeSerializer

User = get_user_model()

ON = dict(FEATURE_VOLUME_PYRAMIDS=True)
# The disabled case, stated explicitly. These used to override only
# MITO_DATA_ROOT and let FEATURE_VOLUME_PYRAMIDS fall through to the
# settings default — off in the `legacy` profile they were written under,
# but on in the deployed `production_integrated_v1`, where pyramids are
# the streaming read path. They therefore asserted disabled behaviour of
# an enabled service and failed under the live profile.
OFF = dict(FEATURE_VOLUME_PYRAMIDS=False)
SHAPE = (8, 128, 128)


def _zarr_available() -> bool:
    try:
        store.require_zarr()
        return True
    except Exception:  # pragma: no cover - environment dependent
        return False


def _roi(shape=SHAPE) -> np.ndarray:
    """A solid ROI block plus a one-voxel-wide stripe.

    The stripe is the interesting part: a thin structure is exactly what a mean
    reduction dissolves and a mode reduction has to keep or drop cleanly.
    """
    mask = np.zeros(shape, dtype=np.uint8)
    mask[:, 16:80, 16:80] = 1
    mask[:, 100, :] = 1
    return mask


class RegionPyramidTestCase(TestCase):
    def setUp(self):
        if not _zarr_available():  # pragma: no cover
            self.skipTest("zarr is an optional dependency and is not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir(parents=True)
        self.external = Path(self.tmp.name) / "src"
        self.external.mkdir()

        rng = np.random.default_rng(11)
        self.source = rng.integers(0, 4000, size=SHAPE, dtype=np.uint16)
        self.image = self.external / "cortex.tif"
        tifffile.imwrite(str(self.image), self.source)

        self.mask = _roi()
        self.region = self.external / "cortex_roi.tif"
        tifffile.imwrite(str(self.region), self.mask)
        self.region_bytes = self.region.read_bytes()

        self.user = User.objects.create_user(username="builder", password="x")
        self.project = Project.objects.create(title="Proj", created_by=self.user)
        self.dataset = Dataset.objects.create(project=self.project, name="DS")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="cortex",
            image_path=str(self.image), region_mask_path=str(self.region),
            voxel_size_z=40.0, voxel_size_y=8.0, voxel_size_x=8.0,
        )


class RegionBuild(RegionPyramidTestCase):
    def test_it_writes_a_sibling_group_and_leaves_the_image_layer_alone(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            report = service.build_pyramid(self.volume, layer="region")
            image_location = store.pyramid_location(self.volume, "image")
            region_location = store.pyramid_location(self.volume, "region")

        self.assertEqual(report.layer, "region")
        self.assertTrue(region_location.path.is_dir())
        self.assertTrue(region_location.rel_path.endswith(".region.zarr"))
        self.assertNotEqual(region_location.rel_path, image_location.rel_path)
        # Building the ROI must not conjure an image derivative.
        self.assertFalse(image_location.path.exists())

        self.volume.refresh_from_db()
        self.assertTrue(self.volume.region_ready_streaming)
        self.assertEqual(self.volume.region_pyramid_metadata["layer"], "region")
        self.assertFalse(self.volume.ready_streaming)
        self.assertEqual(self.volume.pyramid_metadata, {})

    def test_mag_1_round_trips_the_mask_exactly(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume, layer="region")
            for z in (0, SHAPE[0] - 1):
                np.testing.assert_array_equal(
                    store.read_plane(self.volume, "1", z, layer="region"),
                    self.mask[z],
                )

    def test_the_source_mask_is_byte_identical_afterwards(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume, layer="region")
        self.assertEqual(self.region.read_bytes(), self.region_bytes)

    def test_it_reduces_by_mode_so_coarse_mags_stay_a_mask(self):
        """Mean would invent values that are neither inside nor outside."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            report = service.build_pyramid(self.volume, layer="region")
            coarse = store.read_plane(self.volume, "2", 0, layer="region")
        self.assertEqual(report.reduction, "mode")
        self.assertEqual(set(np.unique(coarse)).difference({0, 1}), set())
        # The solid block survives downsampling; that is the ROI, not noise.
        self.assertTrue(coarse.any())

    def test_both_layers_can_be_ready_at_once_and_stay_separate(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
            service.build_pyramid(self.volume, layer="region")
            image_plane = store.read_plane(self.volume, "1", 2)
            region_plane = store.read_plane(self.volume, "1", 2, layer="region")

        self.volume.refresh_from_db()
        self.assertTrue(self.volume.ready_streaming)
        self.assertTrue(self.volume.region_ready_streaming)
        np.testing.assert_array_equal(image_plane, self.source[2])
        np.testing.assert_array_equal(region_plane, self.mask[2])

    def test_clearing_one_layer_leaves_the_other_ready(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
            service.build_pyramid(self.volume, layer="region")
            self.assertTrue(service.clear_pyramid(self.volume, layer="region"))
            self.assertTrue(store.pyramid_location(self.volume, "image").path.exists())

        self.volume.refresh_from_db()
        self.assertTrue(self.volume.ready_streaming)
        self.assertFalse(self.volume.region_ready_streaming)
        self.assertEqual(self.volume.region_pyramid_metadata, {})

    def test_a_volume_without_a_mask_is_refused_by_reason_not_by_crash(self):
        bare = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="bare",
            image_path=str(self.image),
        )
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            with self.assertRaises(service.PyramidBuildError) as ctx:
                service.build_pyramid(bare, layer="region")
        self.assertEqual(ctx.exception.reason, "no_region_mask")
        bare.refresh_from_db()
        self.assertFalse(bare.region_ready_streaming)

    def test_an_unknown_layer_is_refused(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            with self.assertRaises(service.PyramidBuildError) as ctx:
                service.build_pyramid(self.volume, layer="labels")
        self.assertEqual(ctx.exception.reason, "unknown_layer")

    def test_disabled_refuses_and_writes_nothing(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **OFF):
            with self.assertRaises(service.PyramidBuildError) as ctx:
                service.build_pyramid(self.volume, layer="region")
            self.assertEqual(ctx.exception.reason, "disabled")
            self.assertFalse(
                store.pyramid_location(self.volume, "region").path.exists()
            )

    def test_a_concurrent_region_build_is_refused(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            location = store.pyramid_location(self.volume, "region")
            location.tmp_path.mkdir(parents=True)
            with self.assertRaises(service.PyramidConflict):
                service.build_pyramid(self.volume, layer="region")
            # An image build is a different store and is not blocked by it.
            service.build_pyramid(self.volume)
        self.volume.refresh_from_db()
        self.assertTrue(self.volume.ready_streaming)


class RegionSourceFormatParity(RegionPyramidTestCase):
    def test_tiff_hdf5_and_nifti_masks_build_the_same_region_pyramid(self):
        import h5py
        import nibabel as nib

        h5_path = self.external / "roi.h5"
        with h5py.File(h5_path, "w") as handle:
            handle.create_dataset("main", data=self.mask, chunks=(1, 64, 64))
        nii_path = self.external / "roi.nii.gz"
        nib.save(nib.Nifti1Image(self.mask.transpose(2, 1, 0), np.eye(4)), str(nii_path))
        before = {h5_path: h5_path.read_bytes(), nii_path: nii_path.read_bytes()}

        others = [
            Volume.objects.create(
                project=self.project, dataset=self.dataset, name=f"cortex-{label}",
                image_path=str(self.image), region_mask_path=str(path),
                voxel_size_z=40.0, voxel_size_y=8.0, voxel_size_x=8.0,
            )
            for label, path in (("h5", h5_path), ("nii", nii_path))
        ]

        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            for volume in (self.volume, *others):
                service.build_pyramid(volume, layer="region")
            expected1 = store.read_plane(self.volume, "1", 3, layer="region")
            expected2 = store.read_plane(self.volume, "2", 3, layer="region")
            for volume in others:
                volume.refresh_from_db()
                self.assertTrue(volume.region_ready_streaming)
                np.testing.assert_array_equal(
                    store.read_plane(volume, "1", 3, layer="region"), expected1
                )
                np.testing.assert_array_equal(
                    store.read_plane(volume, "2", 3, layer="region"), expected2
                )

        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents, f"{path.name} was rewritten")


class RegionJobs(RegionPyramidTestCase):
    def test_an_image_build_does_not_block_a_region_build(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            image_job = jobs.submit_build(jobs.PyramidBuildSpec(volume_id=self.volume.pk))
            region_job = jobs.submit_build(
                jobs.PyramidBuildSpec(volume_id=self.volume.pk, layer="region")
            )
            # …but a second build of the *same* layer still is.
            with self.assertRaises(jobs.DuplicateBuild):
                jobs.submit_build(
                    jobs.PyramidBuildSpec(volume_id=self.volume.pk, layer="region")
                )
        self.assertNotEqual(image_job.pk, region_job.pk)
        self.assertEqual(jobs.job_layer(image_job), "image")
        self.assertEqual(jobs.job_layer(region_job), "region")

    def test_running_a_region_job_builds_and_promotes_that_layer(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            job = jobs.submit_build(
                jobs.PyramidBuildSpec(volume_id=self.volume.pk, layer="region")
            )
            outcome = jobs.run_build(job)
        job.refresh_from_db()
        self.volume.refresh_from_db()
        self.assertEqual(job.status, ProcessingJobStatus.SUCCEEDED)
        self.assertEqual(outcome["layer"], "region")
        self.assertEqual(outcome["reduction"], "mode")
        self.assertTrue(self.volume.region_ready_streaming)

    def test_a_region_job_whose_mask_vanished_is_skipped_not_failed(self):
        """An image-only volume must not wear a red build status."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            job = jobs.submit_build(
                jobs.PyramidBuildSpec(volume_id=self.volume.pk, layer="region")
            )
            Volume.objects.filter(pk=self.volume.pk).update(region_mask_path="")
            outcome = jobs.run_build(job)
        job.refresh_from_db()
        self.assertEqual(job.status, ProcessingJobStatus.SUCCEEDED)
        self.assertEqual(outcome["skipped"], "no_region_mask")
        self.volume.refresh_from_db()
        self.assertFalse(self.volume.region_ready_streaming)

    def test_registration_enqueues_both_layers_and_only_when_there_is_a_mask(self):
        from volumes.services import register_volume

        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            paired = register_volume(
                project=self.project, dataset=self.dataset, name="paired",
                image_path=str(self.image), region_mask_path=str(self.region),
                created_by=self.user,
            )
            bare = register_volume(
                project=self.project, dataset=self.dataset, name="bare",
                image_path=str(self.image), created_by=self.user,
            )

        def layers_for(volume):
            return sorted(
                jobs.job_layer(job)
                for job in ProcessingJob.objects.filter(
                    volume=volume, job_type=ProcessingJobType.BUILD_PYRAMID
                )
            )

        self.assertEqual(layers_for(paired), ["image", "region"])
        self.assertEqual(layers_for(bare), ["image"])

    def test_changing_the_mask_retires_the_stale_derivative_and_requeues(self):
        """A pyramid of the *previous* ROI is worse than none — it looks right."""
        from volumes.services import update_volume_metadata

        replacement = self.external / "cortex_roi_v2.tif"
        tifffile.imwrite(str(replacement), np.ones(SHAPE, dtype=np.uint8))

        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume, layer="region")
            self.assertTrue(store.pyramid_location(self.volume, "region").path.exists())
            update_volume_metadata(self.volume, region_mask_path=str(replacement))

        self.volume.refresh_from_db()
        self.assertFalse(self.volume.region_ready_streaming)
        self.assertEqual(self.volume.region_pyramid_metadata, {})
        self.assertFalse(store.pyramid_location(self.volume, "region").path.exists())
        queued = ProcessingJob.objects.filter(
            volume=self.volume,
            job_type=ProcessingJobType.BUILD_PYRAMID,
            status=ProcessingJobStatus.QUEUED,
        )
        self.assertEqual([jobs.job_layer(job) for job in queued], ["region"])


    def test_a_registered_volume_whose_mask_changes_is_actually_requeued(self):
        """The regression: registration and the mask change shared one key.

        ``_existing_for_key`` handed the *succeeded* registration job back to
        the requeue, so the derivative was cleared and nothing was ever built.
        A volume created by ``Volume.objects.create`` has no registration job
        and hid the bug entirely — this one registers first, like production.
        """
        from volumes.services import register_volume, update_volume_metadata

        replacement = self.external / "cortex_roi_v3.tif"
        tifffile.imwrite(str(replacement), np.ones(SHAPE, dtype=np.uint8))

        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            volume = register_volume(
                project=self.project, dataset=self.dataset, name="registered",
                image_path=str(self.image), region_mask_path=str(self.region),
                created_by=self.user,
            )
            region_jobs = ProcessingJob.objects.filter(
                volume=volume, job_type=ProcessingJobType.BUILD_PYRAMID
            )
            ProcessingJob.objects.filter(
                pk__in=[j.pk for j in region_jobs]
            ).update(status=ProcessingJobStatus.SUCCEEDED)

            update_volume_metadata(volume, region_mask_path=str(replacement))

        queued = [
            jobs.job_layer(job)
            for job in ProcessingJob.objects.filter(
                volume=volume,
                job_type=ProcessingJobType.BUILD_PYRAMID,
                status=ProcessingJobStatus.QUEUED,
            )
        ]
        self.assertEqual(queued, ["region"])


class RegionBackfill(RegionPyramidTestCase):
    """Legacy volumes earn their ROI build without being re-registered."""

    def backfill(self, **kwargs):
        from volumes.services import backfill_region_pyramids

        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            return backfill_region_pyramids(**kwargs)

    def region_jobs(self, volume):
        return [
            job
            for job in ProcessingJob.objects.filter(
                volume=volume, job_type=ProcessingJobType.BUILD_PYRAMID
            )
            if jobs.job_layer(job) == "region"
        ]

    def test_it_queues_the_volumes_with_a_mask_and_no_roi_stream(self):
        bare = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="bare",
            image_path=str(self.image),
        )
        ready = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="ready",
            image_path=str(self.image), region_mask_path=str(self.region),
            region_ready_streaming=True,
        )

        report = self.backfill()

        self.assertEqual(report["eligible"], [self.volume.pk])
        self.assertEqual(report["queued"], [self.volume.pk])
        self.assertEqual(len(self.region_jobs(self.volume)), 1)
        self.assertEqual(self.region_jobs(bare), [])
        self.assertEqual(self.region_jobs(ready), [])

    def test_a_dry_run_reports_without_queueing_anything(self):
        report = self.backfill(dry_run=True)
        self.assertEqual(report["queued"], [self.volume.pk])
        self.assertEqual(ProcessingJob.objects.count(), 0)

    def test_running_it_twice_does_not_duplicate_an_in_flight_build(self):
        first = self.backfill()
        second = self.backfill()
        self.assertEqual(first["queued"], [self.volume.pk])
        self.assertEqual(second["queued"], [])
        self.assertEqual(second["in_flight"], [self.volume.pk])
        self.assertEqual(len(self.region_jobs(self.volume)), 1)

    def test_a_failed_backfill_can_be_retried_rather_than_replayed_forever(self):
        self.backfill()
        ProcessingJob.objects.filter(volume=self.volume).update(
            status=ProcessingJobStatus.FAILED, error_message="mask unreadable"
        )

        report = self.backfill()

        self.assertEqual(report["queued"], [self.volume.pk])
        self.assertEqual(len(self.region_jobs(self.volume)), 2)

    def test_limit_leaves_the_rest_for_the_next_pass(self):
        other = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="second",
            image_path=str(self.image), region_mask_path=str(self.region),
        )
        report = self.backfill(limit=1)
        self.assertEqual(len(report["eligible"]), 2)
        self.assertEqual(len(report["queued"]), 1)
        self.assertEqual(report["skipped"], [other.pk])

    def test_a_mask_registered_only_as_an_uploaded_file_still_counts(self):
        """``region_mask_file`` is nullable; a NULL must not hide the volume."""
        from volumes.services import volumes_missing_region_pyramid

        Volume.objects.filter(pk=self.volume.pk).update(region_mask_file=None)
        eligible = volumes_missing_region_pyramid()
        self.assertIn(self.volume.pk, [v.pk for v in eligible])

    def test_a_legacy_volume_reaches_ready_without_being_re_registered(self):
        """Image already streaming, ROI not built: the whole point."""
        Volume.objects.filter(pk=self.volume.pk).update(ready_streaming=True)

        report = self.backfill()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            for job in self.region_jobs(self.volume):
                jobs.run_build(job)

        self.volume.refresh_from_db()
        self.assertEqual(report["queued"], [self.volume.pk])
        self.assertTrue(self.volume.region_ready_streaming)
        self.assertTrue(self.volume.ready_streaming)
        self.assertEqual(self.region_jobs(self.volume)[0].config["trigger"], "backfill")

    def test_the_command_selects_by_project_and_reports_its_plan(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            call_command(
                "backfill_region_pyramids",
                "--project",
                str(self.project.pk),
                "--dry-run",
                stdout=out,
            )
        self.assertIn("Eligible (region mask, not streaming): 1", out.getvalue())
        self.assertIn("Would queue: 1", out.getvalue())
        self.assertEqual(ProcessingJob.objects.count(), 0)


class RegionStatusPayload(RegionPyramidTestCase):
    def status(self, volume):
        return VolumeSerializer(volume).data

    def test_a_volume_without_a_mask_reports_absent_not_unbuilt(self):
        bare = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="bare",
            image_path=str(self.image),
        )
        data = self.status(bare)
        self.assertEqual(data["region_streaming_status"], "absent")
        self.assertEqual(data["streaming_status"], "not_built")

    def test_statuses_are_reported_per_layer(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume, layer="region")
            failed = jobs.submit_build(jobs.PyramidBuildSpec(volume_id=self.volume.pk))
        ProcessingJob.objects.filter(pk=failed.pk).update(
            status=ProcessingJobStatus.FAILED, error_message="source unreadable"
        )
        self.volume.refresh_from_db()

        data = self.status(self.volume)
        self.assertEqual(data["region_streaming_status"], "ready")
        self.assertEqual(data["region_streaming_error"], "")
        self.assertEqual(data["streaming_status"], "failed")
        self.assertEqual(data["streaming_error"], "source unreadable")

    def test_a_queued_region_job_reads_as_building(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            jobs.submit_build(
                jobs.PyramidBuildSpec(volume_id=self.volume.pk, layer="region")
            )
        data = self.status(self.volume)
        self.assertEqual(data["region_streaming_status"], "building")
        self.assertEqual(data["streaming_status"], "not_built")
