"""Registering a real HDF5 dataset, end to end: scan -> register -> metadata
-> tasks -> view -> annotate.

``test_hdf5_source.py`` covers the IO layer on single-array files. This module
covers the path a manager actually walks, on the layout the ``nag`` data
arrives in — three sibling directories, one file per crop:

    p10_batch1/<crop>_im.h5              raw image      (dataset "main")
    p10_batch1_region/<crop>_mask_pc2.h5 region mask    (dataset "main")
    p10_batch1_predictions/<crop>_im_xy.h5 prediction   (dataset "data")

Registering that used to "succeed" and then leave managers stuck: the volumes
existed but had no ``shape_z``, so ``ensure_volume_tasks`` skipped every one of
them and no task could be created or assigned, with nothing anywhere saying
why. The usual cause was not the files — it was that the service account could
not *read* them (mode 0640 inside a world-listable directory), which reads
identically to "unsupported file" through a best-effort header reader.

So the assertions here are about the whole chain, and specifically about the
two failure modes that are invisible one step at a time: metadata that is
silently absent, and a shape that never gets a second look once the underlying
permission problem is fixed.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import numpy as np
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from annotation.services import create_whole_volume_task, ensure_volume_tasks
from annotation.visualization import slice_io
from core.choices import FileFormat
from projects.models import Project
from volumes.models import Volume
from volumes.services import register_dataset, scan_data_sources

h5py = None
try:
    import h5py  # noqa: F811
except ImportError:  # pragma: no cover - only on an incomplete environment
    pass

User = get_user_model()

# Small, but shaped like the real thing: a z-stack with chunking, so the
# lazy-plane read path is the one under test rather than a whole-file slurp.
SHAPE = (8, 24, 24)
CROPS = ("96-352_0-2048_9216-11264", "352-608_2048-4096_7168-9216")


def _decode_rgba_png(data: bytes) -> np.ndarray:
    """Decode an RGBA PNG to ``(h, w, 4)``.

    ``slice_io.encode_png`` writes these by hand (no Pillow dependency), so
    the tests decode them by hand too rather than adding one.
    """
    import struct
    import zlib

    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, chunks, width, height = 8, [], 0, 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, _depth, color_type = struct.unpack(">IIBB", payload[:10])
            assert color_type == 6, f"expected RGBA, got color type {color_type}"
        elif kind == b"IDAT":
            chunks.append(payload)
        elif kind == b"IEND":
            break
        pos += 12 + length

    raw = zlib.decompress(b"".join(chunks))
    stride = width * 4
    out = np.zeros((height, stride), dtype=np.uint8)
    prior = np.zeros(stride, dtype=np.uint8)
    at = 0
    for row in range(height):
        filter_type = raw[at]
        line = np.frombuffer(raw[at + 1:at + 1 + stride], dtype=np.uint8).copy()
        at += 1 + stride
        if filter_type == 0:
            pass
        elif filter_type == 1:  # Sub
            for i in range(4, stride):
                line[i] = (int(line[i]) + int(line[i - 4])) & 0xFF
        elif filter_type == 2:  # Up
            line = ((line.astype(int) + prior.astype(int)) & 0xFF).astype(np.uint8)
        else:  # pragma: no cover - encode_png only emits None/Sub/Up
            raise AssertionError(f"unhandled PNG filter {filter_type}")
        out[row] = line
        prior = line
    return out.reshape(height, width, 4)


def _write(path: Path, array: np.ndarray, dataset: str) -> Path:
    with h5py.File(str(path), "w") as handle:
        handle.create_dataset(
            dataset, data=array, chunks=(4, 8, 8), compression="gzip"
        )
    return path


class NagLayoutTestCase(TestCase):
    """Three sibling directories of ``.h5`` crops, as the pipeline writes them."""

    def setUp(self):
        if h5py is None:  # pragma: no cover
            self.skipTest("h5py is not installed")
        self.tmp = tempfile.TemporaryDirectory(prefix="mito-h5-e2e-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(slice_io.clear_caches)
        from core.utils import clear_header_cache

        clear_header_cache()
        self.addCleanup(clear_header_cache)

        base = Path(self.tmp.name)
        self.root = base / "data"
        self.root.mkdir(parents=True)
        self.images = base / "p10_batch1"
        self.regions = base / "p10_batch1_region"
        self.predictions = base / "p10_batch1_predictions"
        for d in (self.images, self.regions, self.predictions):
            d.mkdir(parents=True)

        rng = np.arange(int(np.prod(SHAPE)))
        for crop in CROPS:
            image = (rng % 251).astype(np.uint8).reshape(SHAPE)
            region = np.zeros(SHAPE, np.uint16)
            region[:, 4:20, 4:20] = 1
            prediction = np.zeros(SHAPE, np.uint16)
            prediction[2:6, 6:12, 6:12] = 7
            # The raw/region files name their dataset "main"; the prediction
            # pipeline writes "data". Both are conventional names the reader
            # already knows — the point is that one dataset never sees the
            # other's naming.
            _write(self.images / f"{crop}_im.h5", image, "main")
            _write(self.regions / f"{crop}_mask_pc2.h5", region, "main")
            _write(self.predictions / f"{crop}_im_xy.h5", prediction, "data")

        self.user = User.objects.create_user(username="h5manager", password="x")

    def _register(self, **kwargs):
        defaults = dict(
            created_by=self.user,
            dataset="p10_batch1",
            image_directory=str(self.images),
            region_mask_directory=str(self.regions),
            mask_directory=str(self.predictions),
            label_type="prediction",
        )
        defaults.update(kwargs)
        return register_dataset(**defaults)


@override_settings(MITO_HDF5_DATASET="")
class ScanAndPairTests(NagLayoutTestCase):
    def test_the_crop_files_pair_across_all_three_directories(self):
        """``_im`` / ``_mask_pc2`` / ``_im_xy`` name the same crop three ways.

        The mask side carries a pipeline suffix (`pc2`) the app cannot know is
        decoration, so the shared case id is a strict *prefix* of the mask's,
        not equal to it. Without that rule every nag crop scanned as unpaired
        and had to be matched by hand, one dropdown at a time.
        """
        from volumes.services import pair_by_case

        images = [f"{c}_im.h5" for c in CROPS]
        pairs, unmatched_images, unmatched_masks, _extra = pair_by_case(
            images, [f"{c}_mask_pc2.h5" for c in CROPS]
        )
        self.assertEqual(unmatched_images, [])
        self.assertEqual(unmatched_masks, [])
        self.assertEqual(
            {(p["image"], p["mask"]) for p in pairs},
            {(f"{c}_im.h5", f"{c}_mask_pc2.h5") for c in CROPS},
        )

        pairs, _, _, _ = pair_by_case(images, [f"{c}_im_xy.h5" for c in CROPS])
        self.assertEqual(
            {(p["image"], p["mask"]) for p in pairs},
            {(f"{c}_im.h5", f"{c}_im_xy.h5") for c in CROPS},
        )

    def test_a_longer_suffix_does_not_pair_unrelated_crops(self):
        """The prefix rule must not degrade into "matches anything"."""
        from volumes.services import pair_by_case

        pairs, unmatched_images, _masks, _extra = pair_by_case(
            ["a_b_im.h5"], ["a_c_mask.h5"]
        )
        self.assertEqual(pairs, [])
        self.assertEqual(unmatched_images, ["a_b_im.h5"])

    def test_scan_lists_every_h5_in_each_directory(self):
        result = scan_data_sources(
            str(self.images), str(self.predictions), str(self.regions)
        )
        self.assertEqual(
            {f["name"] for f in result["image_files"]},
            {f"{c}_im.h5" for c in CROPS},
        )
        self.assertEqual(
            {f["name"] for f in result["region_mask_files"]},
            {f"{c}_mask_pc2.h5" for c in CROPS},
        )
        self.assertEqual(
            {f["name"] for f in result["mask_files"]},
            {f"{c}_im_xy.h5" for c in CROPS},
        )
        self.assertEqual({f["extension"] for f in result["image_files"]}, {".h5"})


@override_settings(MITO_HDF5_DATASET="")
class RegistrationMetadataTests(NagLayoutTestCase):
    """Registration must record the shape assignment depends on."""

    def test_registering_records_shape_and_format(self):
        with override_settings(MITO_DATA_ROOT=self.root):
            _project, volumes = self._register(
                pairs=[
                    {
                        "image": f"{c}_im.h5",
                        "region_mask": f"{c}_mask_pc2.h5",
                        "mask": f"{c}_im_xy.h5",
                    }
                    for c in CROPS
                ]
            )
        self.assertEqual(len(volumes), 2)
        for volume in volumes:
            volume.refresh_from_db()
            self.assertEqual(volume.file_format, FileFormat.HDF5)
            # (x, y, z) — the order registration stores, matching TIFF.
            self.assertEqual(
                (volume.shape_x, volume.shape_y, volume.shape_z),
                (SHAPE[2], SHAPE[1], SHAPE[0]),
            )
            self.assertTrue(volume.region_mask_path.endswith("_mask_pc2.h5"))
            self.assertTrue(volume.label_path.endswith("_im_xy.h5"))

    def test_registered_volumes_become_assignable_tasks(self):
        """The symptom that started this: volumes registered, tasks never."""
        with override_settings(MITO_DATA_ROOT=self.root):
            project, volumes = self._register(
                pairs=[{"image": f"{c}_im.h5"} for c in CROPS]
            )
            result = ensure_volume_tasks(project)
        self.assertEqual(result, {"created": 2, "skipped": 0})
        for volume in volumes:
            task = volume.tasks.get()
            self.assertEqual((task.z_start, task.z_end), (0, SHAPE[0]))
            self.assertEqual((task.y_start, task.y_end), (0, SHAPE[1]))
            self.assertEqual((task.x_start, task.x_end), (0, SHAPE[2]))

    def test_a_voxel_size_in_the_file_is_recorded(self):
        path = self.images / f"{CROPS[0]}_im.h5"
        path.unlink()
        with h5py.File(str(path), "w") as handle:
            ds = handle.create_dataset(
                "main", data=np.zeros(SHAPE, np.uint8), chunks=(4, 8, 8)
            )
            ds.attrs["element_size_um"] = np.array([0.04, 0.008, 0.008])
        with override_settings(MITO_DATA_ROOT=self.root):
            _project, volumes = self._register(
                pairs=[{"image": f"{CROPS[0]}_im.h5"}]
            )
        volumes[0].refresh_from_db()
        self.assertAlmostEqual(volumes[0].voxel_size_z, 0.04)
        self.assertAlmostEqual(volumes[0].voxel_size_x, 0.008)


@override_settings(MITO_HDF5_DATASET="")
class UnreadableSourceRecoveryTests(NagLayoutTestCase):
    """A source the process cannot read yet must not be a permanent dead end.

    Registration is best-effort about headers on purpose, but the result was a
    volume with no shape that nothing ever looked at again — so a permission
    fix applied ten minutes later changed nothing, and re-registering was the
    only way out.
    """

    def _register_one_unreadable(self):
        path = self.images / f"{CROPS[0]}_im.h5"
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, stat.S_IRUSR | stat.S_IWUSR)
        with override_settings(MITO_DATA_ROOT=self.root):
            project, volumes = self._register(
                pairs=[{"image": f"{CROPS[0]}_im.h5"}]
            )
        volumes[0].refresh_from_db()
        return project, volumes[0], path

    def test_an_unreadable_source_registers_without_a_shape(self):
        if os.geteuid() == 0:  # pragma: no cover - root ignores mode bits
            self.skipTest("running as root: file modes do not restrict reads")
        _project, volume, _path = self._register_one_unreadable()
        self.assertIsNone(volume.shape_z)
        # Registration itself still succeeded — a header that cannot be read
        # must not lose the reference to the file.
        self.assertTrue(volume.image_path.endswith("_im.h5"))

    def test_creating_a_task_re_reads_the_header_once_it_is_readable(self):
        if os.geteuid() == 0:  # pragma: no cover
            self.skipTest("running as root: file modes do not restrict reads")
        _project, volume, path = self._register_one_unreadable()
        with override_settings(MITO_DATA_ROOT=self.root):
            self.assertIsNone(create_whole_volume_task(volume))

            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            from core.utils import clear_header_cache

            clear_header_cache()
            task = create_whole_volume_task(volume)

        self.assertIsNotNone(task)
        volume.refresh_from_db()
        self.assertEqual(volume.shape_z, SHAPE[0])
        self.assertEqual((task.z_start, task.z_end), (0, SHAPE[0]))

    def test_backfill_command_reports_before_it_writes(self):
        if os.geteuid() == 0:  # pragma: no cover
            self.skipTest("running as root: file modes do not restrict reads")
        _project, volume, path = self._register_one_unreadable()
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        from core.utils import clear_header_cache

        clear_header_cache()
        with override_settings(MITO_DATA_ROOT=self.root):
            call_command("backfill_volume_shapes")
            volume.refresh_from_db()
            # Dry run by default: reported, not recorded.
            self.assertIsNone(volume.shape_z)

            call_command("backfill_volume_shapes", "--apply")
            volume.refresh_from_db()
        self.assertEqual(volume.shape_z, SHAPE[0])

    def test_backfill_leaves_a_still_unreadable_volume_alone(self):
        if os.geteuid() == 0:  # pragma: no cover
            self.skipTest("running as root: file modes do not restrict reads")
        _project, volume, _path = self._register_one_unreadable()
        with override_settings(MITO_DATA_ROOT=self.root):
            call_command("backfill_volume_shapes", "--apply")
        volume.refresh_from_db()
        self.assertIsNone(volume.shape_z)
        # And the row is untouched otherwise — no half-written shape.
        self.assertIsNone(volume.shape_x)


@override_settings(MITO_HDF5_DATASET="")
class ViewAndAnnotateTests(NagLayoutTestCase):
    """The registered crops must open in View and Annotate like a TIFF."""

    def setUp(self):
        super().setUp()
        with override_settings(MITO_DATA_ROOT=self.root):
            self.project, volumes = self._register(
                pairs=[
                    {
                        "image": f"{CROPS[0]}_im.h5",
                        "region_mask": f"{CROPS[0]}_mask_pc2.h5",
                        "mask": f"{CROPS[0]}_im_xy.h5",
                    }
                ]
            )
            self.volume = volumes[0]
            self.task = create_whole_volume_task(self.volume)
        self.task.assigned_to = self.user
        self.task.save(update_fields=["assigned_to"])
        self.client.force_login(self.user)

    def test_volume_meta_reports_the_real_shape(self):
        with override_settings(MITO_DATA_ROOT=self.root):
            resp = self.client.get(f"/api/volumes/{self.volume.pk}/meta/")
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        data = resp.json()
        self.assertEqual(data["shape"], {"z": SHAPE[0], "y": SHAPE[1], "x": SHAPE[2]})
        self.assertTrue(data["has_label"])
        self.assertTrue(data["has_region_mask"])

    def test_image_and_region_slices_render(self):
        with override_settings(MITO_DATA_ROOT=self.root):
            image = self.client.get(
                f"/api/volumes/{self.volume.pk}/slice/", {"axis": "z", "index": 3}
            )
            region = self.client.get(
                f"/api/volumes/{self.volume.pk}/region-mask-slice/",
                {"axis": "z", "index": 3},
            )
        for resp in (image, region):
            self.assertEqual(resp.status_code, 200, resp.content[:300])
            self.assertGreater(len(resp.content), 0)
        self.assertEqual(image["Content-Type"], "image/jpeg")
        self.assertEqual(region["Content-Type"], "image/png")

    def test_the_region_overlay_is_actually_drawn_not_just_returned(self):
        """A fully transparent overlay is a 200 with bytes in it.

        The region layer reaches the browser as an ``<img>`` drawn over the
        canvas, so "the endpoint works" and "the user sees the region" are
        different claims: a renderer that lost the mask still returns a
        perfectly valid, perfectly invisible PNG. Decode it and count opaque
        pixels — and check an empty plane stays empty, so the assertion can
        tell the two apart rather than passing on anything non-zero.
        """
        from annotation.visualization.slice_io import render_region_mask_slice_png

        def opaque_pixels(index: int) -> int:
            rgba = _decode_rgba_png(
                render_region_mask_slice_png(
                    self.volume.region_mask_location, "z", index
                )
            )
            self.assertEqual(rgba.shape, (SHAPE[1], SHAPE[2], 4))
            return int((rgba[:, :, 3] > 0).sum())

        with override_settings(MITO_DATA_ROOT=self.root):
            drawn = opaque_pixels(3)
        # The fixture's region is a 16x16 box on every plane.
        self.assertEqual(drawn, 16 * 16)

    def test_a_plane_the_region_does_not_cover_renders_empty(self):
        """Sparse region masks are normal — real ones cover only part of z.

        This is the case that gets reported as "the region mask is broken":
        opening a volume at z=0 when the mask starts halfway up shows nothing
        at all. Pin the behaviour so a genuinely blank overlay stays
        distinguishable from a genuinely blank *plane*.
        """
        from annotation.visualization.slice_io import render_region_mask_slice_png

        blank = self.regions / f"{CROPS[0]}_mask_pc2.h5"
        blank.unlink()
        sparse = np.zeros(SHAPE, np.uint16)
        sparse[5:, 4:20, 4:20] = 1  # nothing below z=5
        _write(blank, sparse, "main")
        slice_io.clear_caches()

        with override_settings(MITO_DATA_ROOT=self.root):
            empty = _decode_rgba_png(
                render_region_mask_slice_png(self.volume.region_mask_location, "z", 1)
            )
            covered = _decode_rgba_png(
                render_region_mask_slice_png(self.volume.region_mask_location, "z", 6)
            )
        self.assertEqual(int((empty[:, :, 3] > 0).sum()), 0)
        self.assertEqual(int((covered[:, :, 3] > 0).sum()), 16 * 16)

    def test_annotate_seeds_a_writable_working_label_from_the_h5_prediction(self):
        """Annotate needs *editable* labels: the h5 prediction is a source, so
        the first touch has to materialise a working copy carrying its ids."""
        with override_settings(MITO_DATA_ROOT=self.root):
            resp = self.client.get(
                f"/api/tasks/{self.task.pk}/label-ids/", {"axis": "z", "index": 3}
            )
            self.assertEqual(resp.status_code, 200, resp.content[:300])
            data = resp.json()
            self.assertEqual(data["shape"], [SHAPE[1], SHAPE[2]])
            self.assertIn(7, {value for value, _count in data["runs"]})

            # And it is writable: paint a different id over the same slice.
            put = self.client.put(
                f"/api/tasks/{self.task.pk}/label-ids/",
                {
                    "axis": "z", "index": 3,
                    "shape": [SHAPE[1], SHAPE[2]],
                    "runs": [[9, 10], [0, SHAPE[1] * SHAPE[2] - 10]],
                    "origin": "manual",
                },
                content_type="application/json",
            )
            self.assertEqual(put.status_code, 200, put.content[:300])
            again = self.client.get(
                f"/api/tasks/{self.task.pk}/label-ids/", {"axis": "z", "index": 3}
            ).json()
        self.assertEqual(again["runs"][0], [9, 10])

    def test_the_h5_sources_are_never_written_to(self):
        import hashlib

        def digest(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        sources = [
            self.images / f"{CROPS[0]}_im.h5",
            self.regions / f"{CROPS[0]}_mask_pc2.h5",
            self.predictions / f"{CROPS[0]}_im_xy.h5",
        ]
        before = [digest(p) for p in sources]
        with override_settings(MITO_DATA_ROOT=self.root):
            self.client.put(
                f"/api/tasks/{self.task.pk}/label-ids/",
                {
                    "axis": "z", "index": 3,
                    "shape": [SHAPE[1], SHAPE[2]],
                    "runs": [[4, 25], [0, SHAPE[1] * SHAPE[2] - 25]],
                    "origin": "manual",
                },
                content_type="application/json",
            )
        self.assertEqual([digest(p) for p in sources], before)


class MultiDatasetFileTests(TestCase):
    """One ``.h5`` holding several volumes — the case a TIFF cannot have.

    Real exports bundle raw and mask (and sometimes a thumbnail) in one file.
    Picking the wrong one annotates the wrong data silently, so the reader
    resolves by convention where it can and refuses by name where it cannot.
    """

    def setUp(self):
        if h5py is None:  # pragma: no cover
            self.skipTest("h5py is not installed")
        self.tmp = tempfile.TemporaryDirectory(prefix="mito-h5-multi-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(slice_io.clear_caches)
        self.dir = Path(self.tmp.name)
        self.raw = (np.arange(int(np.prod(SHAPE))) % 251).astype(np.uint8).reshape(SHAPE)
        self.seg = np.zeros(SHAPE, np.uint16)
        self.seg[3, 5:9, 5:9] = 4

    def test_a_conventional_name_wins_over_a_sibling_volume(self):
        from annotation.visualization.hdf5_io import open_hdf5_volume

        path = self.dir / "bundle.h5"
        with h5py.File(str(path), "w") as handle:
            handle.create_dataset("main", data=self.raw)
            handle.create_dataset("segmentation_v3", data=self.seg)
        view = open_hdf5_volume(path)
        self.addCleanup(view.close)
        np.testing.assert_array_equal(np.asarray(view), self.raw)

    def test_two_unconventional_volumes_are_refused_by_name(self):
        from annotation.visualization.hdf5_io import Hdf5Error, open_hdf5_volume

        path = self.dir / "ambiguous.h5"
        with h5py.File(str(path), "w") as handle:
            handle.create_dataset("channel_a", data=self.raw)
            handle.create_dataset("channel_b", data=self.raw)
        with self.assertRaises(Hdf5Error) as ctx:
            open_hdf5_volume(path)
        message = str(ctx.exception)
        self.assertIn("channel_a", message)
        self.assertIn("channel_b", message)
        self.assertIn("MITO_HDF5_DATASET", message)

    def test_the_setting_picks_one_and_registration_then_reads_its_shape(self):
        from core.utils import clear_header_cache, inspect_volume_shape

        path = self.dir / "pick.h5"
        with h5py.File(str(path), "w") as handle:
            handle.create_dataset("channel_a", data=self.raw)
            handle.create_dataset("channel_b", data=np.zeros((2, 2, 2), np.uint8))
        clear_header_cache()
        self.addCleanup(clear_header_cache)
        with override_settings(MITO_HDF5_DATASET="channel_a"):
            self.assertEqual(
                inspect_volume_shape(path), (SHAPE[2], SHAPE[1], SHAPE[0])
            )

    def test_a_nested_group_is_found(self):
        from annotation.visualization.hdf5_io import open_hdf5_volume

        path = self.dir / "nested.h5"
        with h5py.File(str(path), "w") as handle:
            handle.create_group("exports").create_dataset("volume", data=self.raw)
        view = open_hdf5_volume(path)
        self.addCleanup(view.close)
        self.assertEqual(view.shape, SHAPE)


class ProjectlessSanityTests(TestCase):
    """Guard the one assumption everything above rests on."""

    def test_a_volume_without_a_shape_is_never_turned_into_a_task(self):
        user = User.objects.create_user(username="sanity", password="x")
        project = Project.objects.create(title="Empty", created_by=user)
        volume = Volume.objects.create(project=project, name="no-image")
        self.assertIsNone(create_whole_volume_task(volume))
        self.assertEqual(ensure_volume_tasks(project), {"created": 0, "skipped": 1})
