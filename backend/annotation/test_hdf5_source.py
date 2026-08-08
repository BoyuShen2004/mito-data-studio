"""HDF5 sources are read in place — never transcoded to a second TIFF copy.

The point of these tests is the *equivalence*: a volume registered as ``.h5``
must behave, everywhere the editor touches it, exactly like the same voxels
registered as ``.tif``. So most cases here write the same array twice, once in
each format, and assert the two paths agree — that is what makes "open an h5 and
edit it like a tif" a claim about behaviour rather than about a code branch.

The other half covers the one thing HDF5 has and TIFF does not: a file can hold
several datasets, so *which* one is the volume has to be decided (and, when it
genuinely cannot be, refused loudly rather than guessed).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from annotation.visualization import slice_io
from annotation.visualization.hdf5_io import (
    Hdf5Error,
    copy_into_label_memmap,
    hdf5_shape_xyz,
    hdf5_voxel_size_zyx,
    is_hdf5_path,
    open_hdf5_volume,
)

h5py = None
try:  # h5py is a hard dependency of the release image; skip cleanly without it.
    import h5py  # noqa: F811
except ImportError:  # pragma: no cover - only on an incomplete environment
    pass

User = get_user_model()

SHAPE = (6, 12, 10)


def _volume_array(dtype=np.uint16) -> np.ndarray:
    return (np.arange(int(np.prod(SHAPE))) % 97).astype(dtype).reshape(SHAPE)


def _write_h5(path: Path, array: np.ndarray, name: str = "main", **kwargs) -> Path:
    with h5py.File(str(path), "w") as handle:
        handle.create_dataset(name, data=array, **kwargs)
    return path


class Hdf5SourceReadTests(SimpleTestCase):
    """``_open_volume`` and everything layered on it, h5 vs tif."""

    def setUp(self):
        if h5py is None:  # pragma: no cover
            self.skipTest("h5py is not installed")
        self.tmp = tempfile.TemporaryDirectory(prefix="mito-h5-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.array = _volume_array()
        self.h5 = _write_h5(self.dir / "vol.h5", self.array)
        self.tif = self.dir / "vol.tif"
        tifffile.imwrite(str(self.tif), self.array)

    def tearDown(self):
        slice_io.clear_caches()

    def test_slices_match_the_same_volume_stored_as_tiff(self):
        for axis, index in (("z", 2), ("y", 5), ("x", 7)):
            with self.subTest(axis=axis):
                np.testing.assert_array_equal(
                    slice_io.read_slice(str(self.h5), axis, index),
                    slice_io.read_slice(str(self.tif), axis, index),
                )

    def test_volume_meta_matches_the_tiff(self):
        self.assertEqual(
            slice_io.volume_meta(str(self.h5)), slice_io.volume_meta(str(self.tif))
        )

    def test_reading_a_slice_does_not_load_the_whole_volume(self):
        """The lazy view must stay lazy: indexing returns a plane, not a copy
        of the file. A regression here (e.g. someone 'simplifying' the wrapper
        into ``np.asarray``) is invisible on a 6x12x10 fixture and fatal on a
        1 GB volume, so assert the read is scoped rather than timing it."""
        view = open_hdf5_volume(self.h5)
        self.addCleanup(view.close)
        plane = view[3]
        self.assertEqual(plane.shape, SHAPE[1:])
        np.testing.assert_array_equal(plane, self.array[3])
        self.assertEqual(view.shape, SHAPE)
        self.assertEqual(view.ndim, 3)
        self.assertEqual(view.size, self.array.size)

    def test_max_matches_numpy_without_materialising(self):
        view = open_hdf5_volume(self.h5)
        self.addCleanup(view.close)
        self.assertEqual(int(view.max()), int(self.array.max()))

    def test_label_rendering_and_max_id_work_on_an_h5_mask(self):
        mask = np.zeros(SHAPE, dtype=np.uint16)
        mask[2, 3:6, 4:8] = 7
        path = _write_h5(self.dir / "mask.h5", mask)

        png = slice_io.render_label_slice_png(str(path), "z", 2)
        self.assertTrue(png.startswith(b"\x89PNG"))

        opened = slice_io._open_volume(path)
        self.assertEqual(slice_io.label_max_id(path, opened), 7)

    def test_read_label_array_returns_int32_without_quarantining_the_source(self):
        mask = np.zeros(SHAPE, dtype=np.uint16)
        mask[1] = 4
        path = _write_h5(self.dir / "label.h5", mask)

        out = slice_io.read_label_array(path)

        self.assertEqual(out.dtype, np.int32)
        np.testing.assert_array_equal(out, mask.astype(np.int32))
        self.assertTrue(path.exists())
        self.assertFalse(path.with_suffix(".h5.corrupt.bak").exists())

    def test_opens_a_read_only_file_without_mutating_it(self):
        """Sources commonly sit on a read-only mount; HDF5's file lock would
        otherwise demand write access to the file's directory."""
        path = _write_h5(self.dir / "ro.h5", self.array)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        os.chmod(path, 0o444)
        self.addCleanup(os.chmod, path, 0o644)

        np.testing.assert_array_equal(
            slice_io.read_slice(str(path), "z", 1), self.array[1]
        )
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_chunked_and_compressed_data_reads_identically(self):
        path = _write_h5(
            self.dir / "chunked.h5",
            self.array,
            chunks=(2, 4, 5),
            compression="gzip",
            compression_opts=4,
        )
        np.testing.assert_array_equal(
            slice_io.read_slice(str(path), "z", 4), self.array[4]
        )

    def test_hdf5_suffix_detection(self):
        self.assertTrue(is_hdf5_path("a/b.h5"))
        self.assertTrue(is_hdf5_path(Path("a/b.HDF5")))
        self.assertFalse(is_hdf5_path("a/b.tif"))


class Hdf5DatasetSelectionTests(SimpleTestCase):
    """Which dataset in the file *is* the volume."""

    def setUp(self):
        if h5py is None:  # pragma: no cover
            self.skipTest("h5py is not installed")
        self.tmp = tempfile.TemporaryDirectory(prefix="mito-h5-pick-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.array = _volume_array()

    def tearDown(self):
        slice_io.clear_caches()

    def test_prefers_a_conventional_name_over_other_datasets(self):
        path = self.dir / "multi.h5"
        with h5py.File(str(path), "w") as handle:
            handle.create_dataset("thumbnail", data=self.array[:1] * 0)
            handle.create_dataset("main", data=self.array)
        view = open_hdf5_volume(path)
        self.addCleanup(view.close)
        self.assertEqual(view.dataset, "main")

    def test_falls_back_to_the_only_3d_dataset(self):
        path = self.dir / "odd-name.h5"
        _write_h5(path, self.array, name="my_volume")
        view = open_hdf5_volume(path)
        self.addCleanup(view.close)
        self.assertEqual(view.dataset, "my_volume")

    def test_finds_a_dataset_nested_in_a_group(self):
        path = self.dir / "nested.h5"
        with h5py.File(str(path), "w") as handle:
            handle.create_group("t0").create_dataset("channel0", data=self.array)
        view = open_hdf5_volume(path)
        self.addCleanup(view.close)
        self.assertEqual(view.dataset, "t0/channel0")

    def test_ambiguous_file_is_refused_and_names_the_candidates(self):
        """Two plausible volumes and no way to choose: guessing here would
        silently annotate the wrong data, so it must fail with the names."""
        path = self.dir / "ambiguous.h5"
        with h5py.File(str(path), "w") as handle:
            handle.create_dataset("channel_a", data=self.array)
            handle.create_dataset("channel_b", data=self.array)

        with self.assertRaises(Hdf5Error) as ctx:
            open_hdf5_volume(path)
        message = str(ctx.exception)
        self.assertIn("channel_a", message)
        self.assertIn("channel_b", message)
        self.assertIn("MITO_HDF5_DATASET", message)

        with self.assertRaises(slice_io.SliceIOError):
            slice_io._open_volume(path)

    @override_settings(MITO_HDF5_DATASET="channel_b")
    def test_setting_resolves_an_ambiguous_file(self):
        path = self.dir / "ambiguous2.h5"
        with h5py.File(str(path), "w") as handle:
            handle.create_dataset("channel_a", data=np.zeros(SHAPE, np.uint16))
            handle.create_dataset("channel_b", data=self.array)

        np.testing.assert_array_equal(
            slice_io.read_slice(str(path), "z", 3), self.array[3]
        )

    def test_leading_singleton_axes_are_dropped(self):
        """nnU-Net-style ``(1, Z, Y, X)`` channel axis."""
        path = self.dir / "channel.h5"
        _write_h5(path, self.array[np.newaxis, ...])
        view = open_hdf5_volume(path)
        self.addCleanup(view.close)
        self.assertEqual(view.shape, SHAPE)
        np.testing.assert_array_equal(view[2], self.array[2])

    def test_a_2d_dataset_is_presented_as_a_single_plane(self):
        path = self.dir / "plane.h5"
        _write_h5(path, self.array[0])
        opened = slice_io._open_volume(path)
        self.assertEqual(opened.shape, (1,) + SHAPE[1:])

    def test_a_file_with_no_volume_is_refused(self):
        path = self.dir / "empty.h5"
        with h5py.File(str(path), "w") as handle:
            handle.create_dataset("scalars", data=np.arange(4))
        with self.assertRaises(Hdf5Error):
            open_hdf5_volume(path)


class Hdf5HeaderInspectionTests(SimpleTestCase):
    """Registration reads shape/spacing from headers, without loading voxels."""

    def setUp(self):
        if h5py is None:  # pragma: no cover
            self.skipTest("h5py is not installed")
        self.tmp = tempfile.TemporaryDirectory(prefix="mito-h5-hdr-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.array = _volume_array()

    def test_shape_is_reported_as_xyz_like_tiff(self):
        from core.utils import clear_header_cache, inspect_volume_shape

        clear_header_cache()
        self.addCleanup(clear_header_cache)
        h5 = _write_h5(self.dir / "vol.h5", self.array)
        tif = self.dir / "vol.tif"
        tifffile.imwrite(str(tif), self.array)

        self.assertEqual(hdf5_shape_xyz(h5), (SHAPE[2], SHAPE[1], SHAPE[0]))
        self.assertEqual(inspect_volume_shape(h5), inspect_volume_shape(tif))

    def test_voxel_size_comes_from_element_size_um_when_present(self):
        from core.utils import clear_header_cache, inspect_volume_voxel_size

        clear_header_cache()
        self.addCleanup(clear_header_cache)
        path = self.dir / "spaced.h5"
        with h5py.File(str(path), "w") as handle:
            ds = handle.create_dataset("main", data=self.array)
            # Fiji/ilastik convention: (z, y, x) in µm.
            ds.attrs["element_size_um"] = np.array([0.04, 0.008, 0.008])

        self.assertEqual(hdf5_voxel_size_zyx(path), (0.04, 0.008, 0.008))
        self.assertEqual(inspect_volume_voxel_size(path), (0.04, 0.008, 0.008))

    def test_missing_spacing_is_none_rather_than_a_guess(self):
        path = _write_h5(self.dir / "bare.h5", self.array)
        self.assertIsNone(hdf5_voxel_size_zyx(path))

    def test_unreadable_file_degrades_to_none(self):
        path = self.dir / "not-really.h5"
        path.write_bytes(b"this is not HDF5")
        self.assertIsNone(hdf5_shape_xyz(path))
        self.assertIsNone(hdf5_voxel_size_zyx(path))


class Hdf5RegistrationTests(SimpleTestCase):
    """The registration gate that used to skip ``.h5`` outright."""

    def test_extension_is_accepted_and_mapped_to_the_hdf5_format(self):
        from core.choices import FileFormat
        from volumes.services import _file_format_for, matched_data_extension

        self.assertEqual(matched_data_extension("cortex_im.h5"), ".h5")
        self.assertEqual(matched_data_extension("cortex_im.HDF5"), ".hdf5")
        self.assertEqual(_file_format_for(".h5"), FileFormat.HDF5)
        self.assertEqual(_file_format_for(".hdf5"), FileFormat.HDF5)

    def test_image_mask_pairing_tokens_still_apply(self):
        """``..._im.h5`` / ``..._mask_pc2.h5`` must classify like their TIFF
        equivalents — pairing keys off name tokens once the extension is
        stripped, and the extension list is what does the stripping."""
        from volumes.services import _is_mask_name

        self.assertTrue(_is_mask_name("96-352_0-2048_9216-11264_mask_pc2.h5"))
        self.assertFalse(_is_mask_name("96-352_0-2048_9216-11264_im.h5"))


class Hdf5WorkingCopyTests(TestCase):
    """Editing: an HDF5 mask seeds a TIFF working copy, and the source is
    left untouched. HDF5 is deliberately *not* made writable — the paint path
    is a plain memmap for a reason (see ``open_label_volume_writable``)."""

    def setUp(self):
        if h5py is None:  # pragma: no cover
            self.skipTest("h5py is not installed")
        from projects.models import Dataset, Project
        from volumes.models import Volume

        self.tmp = tempfile.TemporaryDirectory(prefix="mito-h5-edit-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir(parents=True)
        self.external = Path(self.tmp.name) / "source"
        self.external.mkdir(parents=True)

        self.image_array = _volume_array(np.uint8)
        self.mask_array = np.zeros(SHAPE, dtype=np.uint16)
        self.mask_array[1, 2:5, 3:7] = 12
        self.mask_array[4, 6:9, 1:4] = 5

        self.image = _write_h5(self.external / "cortex_im.h5", self.image_array)
        self.mask = _write_h5(
            self.external / "cortex_mask.h5", self.mask_array, compression="gzip"
        )

        self.user = User.objects.create_user(username="h5editor", password="x")
        self.project = Project.objects.create(title="P", created_by=self.user)
        self.dataset = Dataset.objects.create(project=self.project, name="DS")
        self.volume = Volume.objects.create(
            project=self.project,
            dataset=self.dataset,
            name="cortex",
            image_path=str(self.image),
            label_path=str(self.mask),
        )
        from annotation.models import AnnotationTask
        from core.choices import TaskType

        self.task = AnnotationTask.objects.create(
            project=self.project,
            volume=self.volume,
            assigned_to=self.user,
            z_start=0,
            z_end=SHAPE[0],
            y_end=SHAPE[1],
            x_end=SHAPE[2],
            task_type=TaskType.MANUAL_ANNOTATION,
        )

    def tearDown(self):
        slice_io.clear_caches()

    def test_working_copy_is_a_tiff_seeded_from_the_h5_mask(self):
        from annotation.label_paths import working_label_rel_path
        from annotation.services import _writable_label

        before = hashlib.sha256(self.mask.read_bytes()).hexdigest()

        with override_settings(MITO_DATA_ROOT=self.root):
            rel = working_label_rel_path(self.volume)
            mm, owned_rel = _writable_label(self.volume, SHAPE)
            working = slice_io.resolve_path(owned_rel)

            # The working copy is a TIFF named after the image stem, with the
            # source extension dropped — not ``cortex_im.h5_mask.tif``.
            self.assertTrue(rel.endswith("cortex_im_mask.tif"), rel)
            self.assertTrue(working.exists())
            np.testing.assert_array_equal(np.asarray(mm), self.mask_array)
            self.assertEqual(
                slice_io.label_max_id(working, mm), int(self.mask_array.max())
            )

        # The registered HDF5 mask is a source: never rewritten, never moved.
        self.assertEqual(hashlib.sha256(self.mask.read_bytes()).hexdigest(), before)

    def test_editing_a_slice_writes_the_tiff_working_copy_only(self):
        from annotation.services import _writable_label

        before = hashlib.sha256(self.mask.read_bytes()).hexdigest()
        with override_settings(MITO_DATA_ROOT=self.root):
            mm, owned_rel = _writable_label(self.volume, SHAPE)
            mm[0, 0, 0] = 99
            mm.flush()
            slice_io.drop_file(slice_io.resolve_path(owned_rel))

            reopened, _ = _writable_label(self.volume, SHAPE)
            self.assertEqual(int(reopened[0, 0, 0]), 99)

        self.assertEqual(hashlib.sha256(self.mask.read_bytes()).hexdigest(), before)

    def test_an_unreadable_h5_mask_starts_an_empty_working_copy(self):
        """Same policy the TIFF branch already applies: a registered label
        this app cannot read means "start empty", never an error out of the
        editor's entry point."""
        from annotation.services import _writable_label

        broken = self.external / "broken_mask.h5"
        broken.write_bytes(b"not hdf5 at all")
        self.volume.label_path = str(broken)
        self.volume.save(update_fields=["label_path"])

        with override_settings(MITO_DATA_ROOT=self.root):
            mm, _ = _writable_label(self.volume, SHAPE)
            self.assertEqual(int(np.asarray(mm).max()), 0)

    def test_whole_volume_fallback_also_reads_the_h5_official_label(self):
        """``_load_or_init_label`` is the other place a registered label is
        read. Before HDF5 support it went through ``tifffile.imread`` too, so
        an h5 mask would have come back as an all-zero volume — silently
        losing the prediction the annotator was meant to correct."""
        from annotation.services import _load_or_init_label

        with override_settings(MITO_DATA_ROOT=self.root):
            out = _load_or_init_label(self.volume, SHAPE)

        np.testing.assert_array_equal(out, self.mask_array.astype(np.int32))

    def test_sequential_plan_readers_do_not_close_the_shared_h5_view(self):
        """`_open_volume` owns cached HDF5 handles; closing one plan reader
        must not poison the next request in the same Gunicorn worker."""
        from annotation.services import _LazyPlanLabels

        with override_settings(MITO_DATA_ROOT=self.root):
            first = _LazyPlanLabels(self.task, "z", [])
            first_plane = first.read_axis("z", 1)
            first.close()
            second = _LazyPlanLabels(self.task, "z", [])
            second_plane = second.read_axis("z", 4)
            second.close()

        np.testing.assert_array_equal(first_plane, self.mask_array[1])
        np.testing.assert_array_equal(second_plane, self.mask_array[4])

    def test_a_mismatched_shape_is_not_used_as_a_seed(self):
        from annotation.services import _writable_label

        wrong = _write_h5(self.external / "wrong.h5", np.ones((3, 4, 5), np.uint16))
        self.volume.label_path = str(wrong)
        self.volume.save(update_fields=["label_path"])

        with override_settings(MITO_DATA_ROOT=self.root):
            mm, _ = _writable_label(self.volume, SHAPE)
            self.assertEqual(tuple(mm.shape), SHAPE)
            self.assertEqual(int(np.asarray(mm).max()), 0)


class Hdf5SeedCopyTests(SimpleTestCase):
    """``copy_into_label_memmap`` — the block-wise seed used above."""

    def setUp(self):
        if h5py is None:  # pragma: no cover
            self.skipTest("h5py is not installed")
        self.tmp = tempfile.TemporaryDirectory(prefix="mito-h5-seed-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_copies_every_plane_and_returns_the_max_id(self):
        array = np.zeros((37, 8, 9), dtype=np.uint16)  # depth not a block multiple
        array[36, 0, 0] = 250  # last plane, so a truncated copy is visible
        array[5, 2, 2] = 40
        source = open_hdf5_volume(_write_h5(self.dir / "src.h5", array))
        self.addCleanup(source.close)

        target = self.dir / "dst.tif"
        mm = tifffile.memmap(str(target), shape=array.shape, dtype=np.uint16, mode="w+")

        max_id = copy_into_label_memmap(mm, source, block=8)

        self.assertEqual(max_id, 250)
        np.testing.assert_array_equal(np.asarray(mm), array)
