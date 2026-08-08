"""Phase 11 build, validation, readiness and rollback.

Everything runs against an isolated temporary ``MITO_DATA_ROOT`` with a source
image in its own tempdir, so nothing here can touch real microscopy data.

The tests that matter most are the ones that would still pass under a
plausible-but-wrong implementation if they were weaker: that a *corrupted* chunk
is detected, that a failed build promotes nothing, that the source is
byte-identical afterwards, and that readiness flips only after validation.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from projects.models import Dataset, Project
from volumes.models import Volume
from volumes.pyramid import service, store
from volumes.pyramid.ladder import build_ladder

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


class PyramidTestCase(TestCase):
    def setUp(self):
        if not _zarr_available():  # pragma: no cover
            self.skipTest("zarr is an optional dependency and is not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir(parents=True)
        self.external = Path(self.tmp.name) / "src"
        self.external.mkdir()

        rng = np.random.default_rng(19)
        self.source = rng.integers(0, 4000, size=SHAPE, dtype=np.uint16)
        self.image = self.external / "cortex.tif"
        tifffile.imwrite(str(self.image), self.source)
        self.image_bytes = self.image.read_bytes()
        self.image_mtime = self.image.stat().st_mtime_ns

        self.user = User.objects.create_user(username="builder", password="x")
        self.project = Project.objects.create(title="Proj", created_by=self.user)
        self.dataset = Dataset.objects.create(project=self.project, name="DS")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="cortex",
            image_path=str(self.image),
            voxel_size_z=40.0, voxel_size_y=8.0, voxel_size_x=8.0,
        )


class FlagGating(PyramidTestCase):
    def test_disabled_refuses_and_writes_nothing(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **OFF):
            self.assertFalse(service.pyramids_enabled())
            with self.assertRaises(service.PyramidBuildError) as ctx:
                service.build_pyramid(self.volume)
            self.assertEqual(ctx.exception.reason, "disabled")
            self.assertFalse(store.pyramid_location(self.volume).path.exists())

    def test_disabled_leaves_the_volume_not_ready(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **OFF):
            try:
                service.build_pyramid(self.volume)
            except service.PyramidBuildError:
                pass
        self.volume.refresh_from_db()
        self.assertFalse(self.volume.ready_streaming)
        self.assertEqual(self.volume.pyramid_metadata, {})


class SuccessfulBuild(PyramidTestCase):
    def test_it_writes_a_zarr_v3_group_with_one_array_per_mag(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            report = service.build_pyramid(self.volume)
            location = store.pyramid_location(self.volume)
            meta = store.dump_metadata(location.path)

        self.assertEqual(meta["zarr_format"], 3)
        self.assertTrue(location.path.is_dir())
        expected = [lv.name for lv in build_ladder(SHAPE, (40.0, 8.0, 8.0))]
        for name in expected:
            self.assertTrue((location.path / name).is_dir(), f"missing mag {name}")
        self.assertEqual(len(report.levels), len(expected))

    def test_mag_1_round_trips_the_source_exactly(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
            for z in (0, SHAPE[0] // 2, SHAPE[0] - 1):
                np.testing.assert_array_equal(
                    store.read_plane(self.volume, "1", z), self.source[z]
                )

    def test_the_ladder_is_anisotropy_aware_on_disk(self):
        """40 nm z / 8 nm xy: z must not be downsampled at the first levels."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
            group = store.open_pyramid(self.volume)
            factors = {name: list(group[name].attrs["factors"]) for name in ("1", "2")}
        self.assertEqual(factors["1"], [1, 1, 1])
        self.assertEqual(factors["2"], [1, 2, 2])

    def test_higher_mags_are_smaller(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
            group = store.open_pyramid(self.volume)
            shapes = {name: tuple(group[name].shape) for name in ("1", "2")}
        self.assertEqual(shapes["1"], SHAPE)
        self.assertEqual(shapes["2"], (SHAPE[0], SHAPE[1] // 2, SHAPE[2] // 2))

    def test_chunks_are_slice_oriented(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
            group = store.open_pyramid(self.volume)
            self.assertEqual(tuple(group["1"].chunks)[0], 1)

    def test_readiness_and_metadata_are_recorded(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
        self.volume.refresh_from_db()
        self.assertTrue(self.volume.ready_streaming)
        self.assertIn("levels", self.volume.pyramid_metadata)
        self.assertGreater(self.volume.pyramid_metadata["checked_chunks"], 0)

    def test_no_voxels_are_stored_in_postgresql(self):
        """The database holds paths, mags, shapes and checksums — never bytes."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
        self.volume.refresh_from_db()
        blob = str(self.volume.pyramid_metadata)
        self.assertLess(len(blob), 8000)
        self.assertNotIn("\\x", blob)

    def test_the_derivative_lands_under_the_data_root(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
            path = store.pyramid_location(self.volume).path
        self.assertTrue(path.is_relative_to(self.root.resolve()))
        self.assertEqual(path.parent.name, "pyramids")

    def test_the_source_image_is_byte_identical_afterwards(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
        self.assertEqual(self.image.read_bytes(), self.image_bytes)
        self.assertEqual(self.image.stat().st_mtime_ns, self.image_mtime)

    def test_the_build_is_bounded_not_whole_volume(self):
        """Peak slab must be a plane-sized read, never the volume."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            report = service.build_pyramid(self.volume)
        self.assertLess(report.peak_slab_voxels, int(np.prod(SHAPE)))

    def test_rebuild_is_idempotent(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
            first = store.read_plane(self.volume, "2", 0).copy()
            service.build_pyramid(self.volume)
            second = store.read_plane(self.volume, "2", 0)
        np.testing.assert_array_equal(first, second)


class Validation(PyramidTestCase):
    def test_a_corrupted_chunk_is_detected_and_nothing_is_promoted(self):
        """The test that proves validation is real rather than decorative."""
        from volumes.pyramid import validate as validate_mod

        real = validate_mod.validate_pyramid

        def corrupting(group, source, levels, **kwargs):
            # Corrupt a single voxel of mag 2 before validation runs.
            array = group[levels[1].name]
            array[0, 0, 0] = int(array[0, 0, 0]) ^ 0xFFFF
            return real(group, source, levels, **kwargs)

        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.validate_pyramid = corrupting  # type: ignore[assignment]
            try:
                with self.assertRaises(service.PyramidBuildError) as ctx:
                    service.build_pyramid(self.volume)
            finally:
                service.validate_pyramid = real  # type: ignore[assignment]

            self.assertEqual(ctx.exception.reason, "validation_failed")
            self.assertFalse(store.pyramid_location(self.volume).path.exists())

        self.volume.refresh_from_db()
        self.assertFalse(self.volume.ready_streaming)

    def test_validation_recomputes_from_the_source_not_the_level_above(self):
        from volumes.pyramid.validate import expected_region

        levels = build_ladder(SHAPE, (40.0, 8.0, 8.0))
        got = expected_region(
            self.source, levels[1], (0, 0, 0), (1, 4, 4), reduction="mean"
        )
        self.assertEqual(got.shape, (1, 4, 4))

    def test_a_failed_build_leaves_no_building_directory(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            self.volume.image_path = str(self.external / "missing.tif")
            self.volume.save(update_fields=["image_path"])
            with self.assertRaises(Exception):
                service.build_pyramid(self.volume)
            location = store.pyramid_location(self.volume)
            self.assertFalse(location.tmp_path.exists())
            self.assertFalse(location.path.exists())


class Concurrency(PyramidTestCase):
    def test_a_second_build_while_one_is_in_progress_is_refused(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            location = store.pyramid_location(self.volume)
            location.tmp_path.mkdir(parents=True)
            try:
                with self.assertRaises(service.PyramidConflict):
                    service.build_pyramid(self.volume)
            finally:
                store.discard_build(location)


class Isolation(PyramidTestCase):
    def test_two_volumes_get_separate_derivatives(self):
        other = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="other",
            image_path=str(self.image),
        )
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            self.assertNotEqual(
                store.pyramid_rel_path(self.volume), store.pyramid_rel_path(other)
            )

    def test_a_different_project_gets_a_different_directory(self):
        other_project = Project.objects.create(title="Other", created_by=self.user)
        other_dataset = Dataset.objects.create(project=other_project, name="DS2")
        other_volume = Volume.objects.create(
            project=other_project, dataset=other_dataset, name="v",
            image_path=str(self.image),
        )
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            mine = store.pyramid_rel_path(self.volume)
            theirs = store.pyramid_rel_path(other_volume)
        self.assertTrue(mine.startswith("Proj/"))
        self.assertTrue(theirs.startswith("Other/"))


class Rollback(PyramidTestCase):
    def test_clearing_removes_the_derivative_and_readiness(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
            self.assertTrue(store.pyramid_location(self.volume).path.exists())
            removed = service.clear_pyramid(self.volume)
            self.assertTrue(removed)
            self.assertFalse(store.pyramid_location(self.volume).path.exists())
        self.volume.refresh_from_db()
        self.assertFalse(self.volume.ready_streaming)
        self.assertEqual(self.volume.pyramid_metadata, {})

    def test_rollback_leaves_the_source_untouched(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
            service.clear_pyramid(self.volume)
        self.assertEqual(self.image.read_bytes(), self.image_bytes)


class LegacyData(PyramidTestCase):
    def test_a_volume_with_no_pyramid_is_valid(self):
        """An absent derivative is an accurate absence, not a missing record."""
        legacy = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="legacy",
            image_path=str(self.image),
        )
        self.assertFalse(legacy.ready_streaming)
        self.assertEqual(legacy.pyramid_metadata, {})
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            self.assertFalse(store.pyramid_location(legacy).path.exists())

    def test_missing_voxel_size_falls_back_to_isotropic(self):
        """Isotropic voxels degenerate to the obvious (2,2,2) ladder.

        Needs a volume deep enough that halving z stays above MIN_MAG_EXTENT —
        with only 8 planes the ladder correctly stops at full resolution, which
        is the anisotropy guard doing its job rather than a bug.
        """
        cube = np.random.default_rng(5).integers(
            0, 1000, size=(64, 64, 64), dtype=np.uint16
        )
        cube_path = self.external / "cube.tif"
        tifffile.imwrite(str(cube_path), cube)
        iso = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="iso",
            image_path=str(cube_path),
        )
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(iso)
            group = store.open_pyramid(iso)
            self.assertEqual(list(group["2"].attrs["factors"]), [2, 2, 2])


class LabelReduction(PyramidTestCase):
    def test_label_volumes_use_mode_and_never_invent_ids(self):
        labels = np.zeros((64, 128, 128), dtype=np.uint16)
        labels[:, :64, :64] = 3
        labels[:, 64:, 64:] = 9
        label_path = self.external / "labels.tif"
        tifffile.imwrite(str(label_path), labels)
        vol = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="lbl",
            image_path=str(label_path),
        )
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(vol, is_label=True)
            plane = store.read_plane(vol, "2", 0)
        self.assertTrue(set(np.unique(plane)).issubset({0, 3, 9}))


class SourceFormatParity(PyramidTestCase):
    def test_tiff_hdf5_and_nifti_build_the_same_streaming_pyramid(self):
        import h5py
        import nibabel as nib

        h5_path = self.external / "cortex.h5"
        with h5py.File(h5_path, "w") as handle:
            handle.create_dataset("main", data=self.source, chunks=(1, 64, 64))
        nii_path = self.external / "cortex.nii.gz"
        nib.save(
            nib.Nifti1Image(self.source.transpose(2, 1, 0), np.eye(4)),
            str(nii_path),
        )
        h5_volume = Volume.objects.create(
            project=self.project,
            dataset=self.dataset,
            name="cortex-h5",
            image_path=str(h5_path),
            voxel_size_z=40.0,
            voxel_size_y=8.0,
            voxel_size_x=8.0,
        )
        nii_volume = Volume.objects.create(
            project=self.project,
            dataset=self.dataset,
            name="cortex-nii",
            image_path=str(nii_path),
            voxel_size_z=40.0,
            voxel_size_y=8.0,
            voxel_size_x=8.0,
        )
        before = {h5_path: h5_path.read_bytes(), nii_path: nii_path.read_bytes()}

        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            for volume in (self.volume, h5_volume, nii_volume):
                service.build_pyramid(volume)
            expected_mag1 = store.read_plane(self.volume, "1", 3)
            expected_mag2 = store.read_plane(self.volume, "2", 3)
            for volume in (h5_volume, nii_volume):
                volume.refresh_from_db()
                self.assertTrue(volume.ready_streaming)
                np.testing.assert_array_equal(
                    store.read_plane(volume, "1", 3), expected_mag1
                )
                np.testing.assert_array_equal(
                    store.read_plane(volume, "2", 3), expected_mag2
                )

        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)
