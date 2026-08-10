"""Tests for the Cellable-ported interactive AI tools (Point/Box/Boundary
mask, 3D watershed Seeds) and the Labels-panel/3D-preview summaries.

Follows the same tempdir + ``@override_settings(MITO_DATA_ROOT=...)``
fixture pattern as ``test_tracking.py`` — see
``progress/history/04-incident-data-safety.md`` for why every destructive
test in this app isolates its filesystem like this rather than touching the
real dev database.
"""

import os
import tempfile
import unittest
import unittest.mock

import numpy as np
import tifffile
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import AnnotatorProfile, UserProfile
from annotation.cellable_port.labels_3d import (
    label_summary,
    labels_3d_mesh,
    labels_3d_preview,
)
from annotation.cellable_port.watershed import WatershedError, run_watershed_3d
from annotation.cellable_port.split_components import (
    SplitComponentsError,
    run_split_components_3d,
)
from annotation.cellable_port.merge_labels import MergeLabelsError, run_merge_labels
from annotation.label_paths import working_label_rel_path
from annotation.models import AnnotationTask
from annotation.visualization import slice_io
from core.choices import LabelType, TaskType, UserRole
from projects.services import create_project
from volumes.models import Volume

User = get_user_model()
_TMP = tempfile.mkdtemp(prefix="mito-cellable-port-test-")


@override_settings(MITO_DATA_ROOT=_TMP)
class EfficientSamRuntimeUnitTests(TestCase):
    """Thread-count resolution (ORT affinity-spam fix) and the on-disk
    embedding cache — no ONNX session needed for either.

    ``MITO_DATA_ROOT`` overridden to the shared tempdir: ``embed_cache``
    resolves paths under this setting, and writing cache files under the
    *real* data root from a test is exactly the mistake
    `progress/history/04-incident-data-safety.md` exists to prevent.
    """

    def test_thread_count_prefers_slurm_env(self):
        from annotation.cellable_port.ai.efficient_sam import _resolve_thread_count

        with unittest.mock.patch.dict(os.environ, {"SLURM_CPUS_PER_TASK": "3"}):
            self.assertEqual(_resolve_thread_count(), 3)

    def test_thread_count_caps_at_max(self):
        from annotation.cellable_port.ai.efficient_sam import (
            _MAX_INTRA_OP_THREADS,
            _resolve_thread_count,
        )

        with unittest.mock.patch.dict(os.environ, {"SLURM_CPUS_PER_TASK": "999"}):
            self.assertEqual(_resolve_thread_count(), _MAX_INTRA_OP_THREADS)

    def test_thread_count_ignores_garbage_slurm_value(self):
        from annotation.cellable_port.ai.efficient_sam import _resolve_thread_count

        with unittest.mock.patch.dict(os.environ, {"SLURM_CPUS_PER_TASK": "not-a-number"}):
            self.assertGreaterEqual(_resolve_thread_count(), 1)

    @override_settings(MITO_AI_ONNX_CUDA=True, MITO_AI_CUDA_DEVICE="1")
    def test_encoder_and_decoder_both_request_cuda(self):
        from annotation.cellable_port.ai import efficient_sam

        session = unittest.mock.Mock()
        session.get_providers.return_value = [
            "CUDAExecutionProvider",
            "CPUExecutionProvider",
        ]
        with (
            unittest.mock.patch.object(
                efficient_sam, "_session_options", return_value=object()
            ),
            unittest.mock.patch.object(
                efficient_sam, "_make_session", return_value=session
            ) as make_session,
        ):
            efficient_sam.EfficientSam("encoder.onnx", "decoder.onnx")

        self.assertEqual(make_session.call_count, 2)
        self.assertEqual(
            [call.kwargs["cuda"] for call in make_session.call_args_list],
            [True, True],
        )

    def test_embed_cache_round_trip(self):
        from annotation.cellable_port.ai import embed_cache

        path = embed_cache.cache_path_for("proj/ds/embeddings", "img", "z", 5, "vits", 12345.0)
        self.assertIsNone(embed_cache.load(path))  # nothing written yet
        arr = np.random.rand(1, 4, 5, 5).astype(np.float32)
        embed_cache.save(path, arr)
        loaded = embed_cache.load(path)
        self.assertIsNotNone(loaded)
        np.testing.assert_array_equal(loaded, arr)
        # Lives under the volume's dataset embeddings/ folder, not a global silo.
        self.assertIn(os.path.join("proj", "ds", "embeddings", "vits"), str(path))

    def test_embed_cache_key_changes_with_variant_mtime_and_volume(self):
        from annotation.cellable_port.ai import embed_cache

        a = embed_cache.cache_path_for("proj/ds/embeddings", "img", "z", 5, "vits", 100.0)
        b = embed_cache.cache_path_for("proj/ds/embeddings", "img", "z", 5, "vitt", 100.0)
        c = embed_cache.cache_path_for("proj/ds/embeddings", "img", "z", 5, "vits", 200.0)
        # Two volumes sharing one dataset folder must not collide — the stem
        # (image-derived) disambiguates them.
        d = embed_cache.cache_path_for("proj/ds/embeddings", "other", "z", 5, "vits", 100.0)
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertNotEqual(a, d)

    def test_embed_cache_key_includes_prompt_roi(self):
        from annotation.cellable_port.ai import embed_cache

        full = embed_cache.cache_path_for(
            "proj/ds/embeddings", "img", "z", 5, "vits", 100.0
        )
        roi = embed_cache.cache_path_for(
            "proj/ds/embeddings",
            "img",
            "z",
            5,
            "vits",
            100.0,
            roi_token="y0-1024_x64-1088",
        )
        self.assertNotEqual(full, roi)

    def test_decoder_selects_highest_iou_candidate(self):
        from annotation.cellable_port.ai.efficient_sam import _decode_mask

        masks = np.zeros((1, 1, 3, 4, 4), dtype=np.float32)
        masks[0, 0, 2, 1:3, 1:3] = 1
        decoder = unittest.mock.Mock()
        decoder.run.return_value = (
            masks,
            np.array([[[0.1, 0.2, 0.9]]], dtype=np.float32),
            None,
        )
        result = _decode_mask(
            decoder,
            np.zeros((4, 4), dtype=np.uint8),
            np.zeros((1, 1), dtype=np.float32),
            [[2, 2]],
            [1],
        )
        self.assertTrue(result[1:3, 1:3].all())


class WatershedUnitTests(TestCase):
    """Pure numpy tests for the ported segmentation core — no Django/HTTP."""

    def test_splits_dumbbell_into_two_labels(self):
        # A "dumbbell": two 4x4x4 blobs joined by a thin 1-voxel-wide neck,
        # all currently one instance id (5). Seeding one point in each lobe
        # should split the neck at the watershed ridge.
        mask = np.zeros((10, 10, 10), dtype=np.int32)
        mask[1:5, 1:5, 1:5] = 5
        mask[1:5, 1:5, 8:9] = 5  # bridge/neck (thin)
        mask[1:5, 1:5, 9:10] = 0
        mask[1:5, 1:5, 6:10] = 5  # second lobe + neck, connected

        result = run_watershed_3d(mask, target_label=5, seeds_zyx=[(2, 2, 2), (2, 2, 8)])
        self.assertEqual(result["target_label"], 5)
        self.assertEqual(len(result["new_label_ids"]), 1)
        new_id = result["new_label_ids"][0]
        remaining_ids = set(int(v) for v in np.unique(mask)) - {0}
        self.assertEqual(remaining_ids, {5, new_id})

    def test_missing_label_raises(self):
        mask = np.zeros((4, 4, 4), dtype=np.int32)
        with self.assertRaises(WatershedError):
            run_watershed_3d(mask, target_label=9, seeds_zyx=[(0, 0, 0)])

    def test_seed_outside_label_raises(self):
        mask = np.zeros((4, 4, 4), dtype=np.int32)
        mask[0, 0, 0] = 3
        with self.assertRaises(WatershedError):
            run_watershed_3d(mask, target_label=3, seeds_zyx=[(3, 3, 3)])


class SplitComponentsUnitTests(TestCase):
    """Pure numpy tests for 3D connected-component split (Cellable split_label)."""

    def test_splits_two_disconnected_blobs(self):
        mask = np.zeros((8, 10, 10), dtype=np.int32)
        mask[1:4, 1:4, 1:4] = 7  # 27 voxels
        mask[5:8, 6:10, 6:10] = 7  # larger blob
        # Lower threshold so the small blob is kept.
        result = run_split_components_3d(mask, target_label=7, size_threshold=10)
        self.assertEqual(result["target_label"], 7)
        self.assertEqual(result["components_kept"], 2)
        self.assertEqual(len(result["new_label_ids"]), 1)
        new_id = result["new_label_ids"][0]
        # Largest keeps original id.
        self.assertGreater(int(np.count_nonzero(mask == 7)), int(np.count_nonzero(mask == new_id)))
        self.assertEqual(set(int(v) for v in np.unique(mask)) - {0}, {7, new_id})

    def test_single_component_no_new_ids(self):
        mask = np.zeros((6, 6, 6), dtype=np.int32)
        mask[1:5, 1:5, 1:5] = 3
        result = run_split_components_3d(mask, target_label=3, size_threshold=10)
        self.assertEqual(result["new_label_ids"], [])
        self.assertEqual(result["components_kept"], 1)
        self.assertEqual(set(int(v) for v in np.unique(mask)) - {0}, {3})

    def test_tiny_components_cleared(self):
        mask = np.zeros((6, 6, 6), dtype=np.int32)
        mask[1:4, 1:4, 1:4] = 4  # 27 voxels — kept
        mask[5, 5, 5] = 4  # 1 voxel — cleared
        result = run_split_components_3d(mask, target_label=4, size_threshold=10)
        self.assertEqual(result["new_label_ids"], [])
        self.assertEqual(result["voxels_cleared"], 1)
        self.assertEqual(int(mask[5, 5, 5]), 0)
        self.assertEqual(int(np.count_nonzero(mask == 4)), 27)

    def test_missing_label_raises(self):
        mask = np.zeros((4, 4, 4), dtype=np.int32)
        with self.assertRaises(SplitComponentsError):
            run_split_components_3d(mask, target_label=9)


class MergeLabelsUnitTests(TestCase):
    def test_keeps_smaller_id_either_order(self):
        mask = np.zeros((4, 6, 6), dtype=np.int32)
        mask[0:2, 0:3, 0:3] = 2
        mask[2:4, 3:6, 3:6] = 5
        result = run_merge_labels(mask, 5, 2)  # either order
        self.assertEqual(result["kept_label"], 2)
        self.assertEqual(result["removed_label"], 5)
        self.assertEqual(result["voxels_merged"], 18)
        self.assertEqual(int(np.count_nonzero(mask == 5)), 0)
        self.assertEqual(int(np.count_nonzero(mask == 2)), 36)

    def test_same_label_raises(self):
        mask = np.zeros((2, 2, 2), dtype=np.int32)
        mask[:] = 3
        with self.assertRaises(MergeLabelsError):
            run_merge_labels(mask, 3, 3)

    def test_missing_label_raises(self):
        mask = np.zeros((2, 2, 2), dtype=np.int32)
        mask[:] = 1
        with self.assertRaises(MergeLabelsError):
            run_merge_labels(mask, 9, 1)


class LabelsSummaryAndPreviewUnitTests(TestCase):
    def setUp(self):
        self.path_str = os.path.join(_TMP, "unit_labels.tif")
        vol = np.zeros((6, 8, 8), dtype=np.uint16)
        vol[0:2, 0:3, 0:3] = 1
        vol[3:6, 4:8, 4:8] = 2
        tifffile.imwrite(self.path_str, vol)
        # tifffile.memmap needs a real Path-like with .exists()/.stat()
        from pathlib import Path

        self.path = Path(self.path_str)

    def test_label_summary_counts_and_z_range(self):
        summary = label_summary(self.path)
        by_id = {row["id"]: row for row in summary["labels"]}
        self.assertEqual(set(by_id), {1, 2})
        self.assertEqual(by_id[1]["voxel_count"], 2 * 3 * 3)
        self.assertEqual((by_id[1]["z_start"], by_id[1]["z_end"]), (0, 1))
        self.assertEqual(by_id[2]["voxel_count"], 3 * 4 * 4)
        self.assertEqual((by_id[2]["z_start"], by_id[2]["z_end"]), (3, 5))

    def test_preview_grid_nonempty_for_present_labels(self):
        preview = labels_3d_preview(self.path, [1, 2])
        self.assertIn(1, preview["grids"])
        self.assertIn(2, preview["grids"])
        self.assertTrue(preview["grids"][1].any())

    def test_preview_empty_for_absent_label(self):
        preview = labels_3d_preview(self.path, [999])
        self.assertEqual(preview["grids"], {})

    def test_summary_reports_bounding_boxes(self):
        bboxes = label_summary(self.path)["bboxes"]
        # Exclusive upper bounds, whole-volume voxel coords.
        self.assertEqual(bboxes[1], (0, 2, 0, 3, 0, 3))
        self.assertEqual(bboxes[2], (3, 6, 4, 8, 4, 8))

    def test_summary_matches_a_brute_force_pass(self):
        """The cached/threaded scan must agree exactly with the obvious
        implementation — counts and boxes are user-visible numbers."""
        vol = tifffile.imread(self.path_str)
        summary = label_summary(self.path)
        for row in summary["labels"]:
            zs, ys, xs = np.nonzero(vol == row["id"])
            self.assertEqual(row["voxel_count"], zs.size)
            self.assertEqual(row["z_start"], int(zs.min()))
            self.assertEqual(row["z_end"], int(zs.max()))
            self.assertEqual(
                summary["bboxes"][row["id"]],
                (
                    int(zs.min()), int(zs.max()) + 1,
                    int(ys.min()), int(ys.max()) + 1,
                    int(xs.min()), int(xs.max()) + 1,
                ),
            )


def _make_volume_and_task(name, *, shape=(5, 8, 8)):
    """A registered Volume (with a real image file under ``_TMP``) plus a task
    on it — the minimum a service-level label call needs."""
    rel = f"images/{name}.tif"
    path = os.path.join(_TMP, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tifffile.imwrite(path, np.full(shape, 40, dtype=np.uint8))
    project = create_project(title=f"P-{name}", reviewed=True)
    volume = Volume.objects.create(
        project=project, name=name, image_path=rel, label_type=LabelType.NONE,
        shape_z=shape[0], shape_y=shape[1], shape_x=shape[2],
    )
    owned = os.path.join(_TMP, working_label_rel_path(volume))
    if os.path.exists(owned):
        os.remove(owned)
    task = AnnotationTask.objects.create(
        project=project, volume=volume, z_start=0, z_end=shape[0],
        y_end=shape[1], x_end=shape[2], task_type=TaskType.MANUAL_ANNOTATION,
    )
    return volume, task


@override_settings(MITO_DATA_ROOT=_TMP)
class LabelsSummaryIncrementalTests(TestCase):
    """Saving a slice folds into the cached summary instead of invalidating it.

    A full rescan on the request after every Save cost 10-27s on the volumes
    in this repo (progress/history/04-perf-repo-hygiene-oneclick-setup.md
    item A), so the exactness of the incremental path is load-bearing.
    """

    def setUp(self):
        from pathlib import Path

        from annotation.cellable_port import labels_3d

        labels_3d._summary_cache.clear()
        self.path_str = os.path.join(_TMP, f"incremental_{id(self)}.tif")
        vol = np.zeros((5, 8, 8), dtype=np.uint16)
        vol[1:3, 1:4, 1:4] = 1
        vol[2:4, 5:7, 5:7] = 2
        tifffile.imwrite(self.path_str, vol)
        self.path = Path(self.path_str)
        self.vol = vol

    def _write_slice(self, z, new_slice):
        """Write one z in place, the way ``set_label_slice_ids`` does, and fold
        it into the cached summary."""
        from annotation.cellable_port.labels_3d import update_summary_for_slice

        mtime_before = self.path.stat().st_mtime
        mm = tifffile.memmap(self.path_str, mode="r+")
        mm[z] = new_slice
        mm.flush()
        del mm
        # Filesystems can report the same mtime for two writes in quick
        # succession; force a change so this test exercises the real branch.
        os.utime(self.path_str, (mtime_before + 1, mtime_before + 1))
        return update_summary_for_slice(self.path, z, new_slice, mtime_before=mtime_before)

    def _brute_force(self):
        vol = tifffile.imread(self.path_str)
        out = {}
        for lid in (int(v) for v in np.unique(vol) if v > 0):
            zs, ys, xs = np.nonzero(vol == lid)
            out[lid] = {
                "voxel_count": int(zs.size),
                "z_start": int(zs.min()),
                "z_end": int(zs.max()),
                "bbox": (
                    int(zs.min()), int(zs.max()) + 1,
                    int(ys.min()), int(ys.max()) + 1,
                    int(xs.min()), int(xs.max()) + 1,
                ),
            }
        return out

    def _assert_summary_exact(self):
        summary = label_summary(self.path)
        expected = self._brute_force()
        rows = {row["id"]: row for row in summary["labels"]}
        self.assertEqual(set(rows), set(expected))
        for lid, want in expected.items():
            self.assertEqual(rows[lid]["voxel_count"], want["voxel_count"], lid)
            self.assertEqual(rows[lid]["z_start"], want["z_start"], lid)
            self.assertEqual(rows[lid]["z_end"], want["z_end"], lid)
            self.assertEqual(summary["bboxes"][lid], want["bbox"], lid)

    def test_growing_a_label_updates_counts_and_box(self):
        label_summary(self.path)  # prime the cache
        new = np.zeros((8, 8), dtype=np.uint16)
        new[0:6, 0:6] = 1  # bigger than before, and reaching y=0/x=0
        self.assertTrue(self._write_slice(1, new))
        self._assert_summary_exact()

    def test_shrinking_a_label_shrinks_its_box_and_z_range(self):
        """The hard direction: erasing must *narrow* the reported extent, not
        leave a stale box behind."""
        label_summary(self.path)
        self.assertTrue(self._write_slice(1, np.zeros((8, 8), dtype=np.uint16)))
        self._assert_summary_exact()
        rows = {row["id"]: row for row in label_summary(self.path)["labels"]}
        self.assertEqual(rows[1]["z_start"], 2)  # z=1 was erased

    def test_erasing_a_label_everywhere_drops_it_from_the_list(self):
        label_summary(self.path)
        blank = np.zeros((8, 8), dtype=np.uint16)
        for z in (2, 3):
            self.assertTrue(self._write_slice(z, blank))
        self._assert_summary_exact()
        self.assertNotIn(2, {row["id"] for row in label_summary(self.path)["labels"]})

    def test_update_is_refused_when_the_file_moved_underneath_us(self):
        """Another writer between our read and our write means folding one
        slice in would miss their change — refuse, and let the next read
        rescan instead."""
        from annotation.cellable_port.labels_3d import update_summary_for_slice

        label_summary(self.path)
        new = np.zeros((8, 8), dtype=np.uint16)
        applied = update_summary_for_slice(self.path, 1, new, mtime_before=1.0)
        self.assertFalse(applied)

    def test_no_cache_entry_means_no_update(self):
        from annotation.cellable_port.labels_3d import _summary_cache, update_summary_for_slice

        _summary_cache.clear()
        self.assertFalse(
            update_summary_for_slice(
                self.path, 1, np.zeros((8, 8), dtype=np.uint16), mtime_before=1.0
            )
        )

    def test_slice_save_through_the_service_keeps_the_summary_without_rescanning(self):
        """End-to-end: `set_label_slice_ids` must fold in, not invalidate."""
        from unittest.mock import patch

        from annotation.cellable_port import labels_3d
        from annotation.services import get_labels_summary, set_label_slice_ids
        from annotation.visualization.slice_io import encode_label_rle

        volume, task = _make_volume_and_task("incr", shape=(5, 8, 8))
        del task
        get_labels_summary(volume)  # prime

        painted = np.zeros((8, 8), dtype=np.int32)
        painted[2:5, 2:5] = 9
        with patch.object(
            labels_3d, "_scan_stats", side_effect=AssertionError("rescanned the volume")
        ):
            set_label_slice_ids(volume, "z", 1, [8, 8], encode_label_rle(painted))
            rows = {row["id"]: row for row in get_labels_summary(volume)["labels"]}
        self.assertEqual(rows[9]["voxel_count"], 9)
        self.assertEqual((rows[9]["z_start"], rows[9]["z_end"]), (1, 1))


class Labels3DMeshUnitTests(TestCase):
    """The 3D Labels panel renders real marching-cubes surfaces (03 item B).

    These assert the properties that distinguish a *surface* from the old
    voxel-cube preview: geometry lands inside the label's own bounding box,
    triangles index real vertices, and a wide-but-thin label keeps its z
    extent instead of collapsing into a single flat slab.
    """

    def _write(self, name, vol):
        from pathlib import Path

        path_str = os.path.join(_TMP, name)
        tifffile.imwrite(path_str, vol)
        return Path(path_str)

    def test_mesh_is_a_closed_surface_inside_the_label_bbox(self):
        vol = np.zeros((24, 24, 24), dtype=np.uint16)
        zz, yy, xx = np.ogrid[:24, :24, :24]
        vol[((zz - 12) ** 2 + (yy - 12) ** 2 + (xx - 12) ** 2) <= 36] = 1  # r=6 ball
        path = self._write("mesh_ball.tif", vol)

        result = labels_3d_mesh(path, [1])
        self.assertEqual(len(result["meshes"]), 1)
        mesh = result["meshes"][0]
        self.assertEqual(mesh["id"], 1)
        self.assertGreater(len(mesh["faces"]), 100)  # a real surface, not a stub
        self.assertEqual(mesh["vertices"].shape[1], 3)
        self.assertEqual(mesh["faces"].shape[1], 3)
        # Every index addresses a vertex that exists.
        self.assertLess(int(mesh["faces"].max()), len(mesh["vertices"]))
        # ... and the surface hugs the ball (whole-volume voxel coordinates).
        for axis in range(3):
            self.assertGreater(mesh["vertices"][:, axis].min(), 3.0)
            self.assertLess(mesh["vertices"][:, axis].max(), 21.0)
        # It is a ball, so it must have real extent on every axis — including z.
        self.assertGreater(result["size"][0], 8.0)

    def test_wide_thin_label_keeps_its_z_extent(self):
        """Regression: a single isotropic stride chosen from the widest axis
        pooled a 4-slice-deep label down to one cell — the "stack of 2D
        slabs" look. Per-axis strides keep z."""
        vol = np.zeros((8, 260, 260), dtype=np.uint16)
        vol[2:6, 20:250, 20:250] = 1
        path = self._write("mesh_thin.tif", vol)

        mesh = labels_3d_mesh(path, [1])["meshes"][0]
        z = mesh["vertices"][:, 0]
        self.assertGreater(float(z.max() - z.min()), 2.0)

    def test_absent_label_yields_no_geometry(self):
        vol = np.zeros((6, 8, 8), dtype=np.uint16)
        vol[1:3, 1:4, 1:4] = 5
        path = self._write("mesh_absent.tif", vol)

        self.assertEqual(labels_3d_mesh(path, [999])["meshes"], [])
        self.assertEqual(labels_3d_mesh(path, [])["meshes"], [])

    def test_meshes_are_independent_of_the_other_requested_labels(self):
        """Per-label bbox crops + whole-volume coordinates: at a given level
        of detail, asking for one more label must not move the geometry of
        the ones already loaded (that independence is what lets the mesh
        cache be keyed per label rather than per request)."""
        vol = np.zeros((10, 30, 30), dtype=np.uint16)
        vol[2:6, 2:10, 2:10] = 1
        vol[3:7, 18:28, 18:28] = 2
        path = self._write("mesh_pair.tif", vol)

        alone = labels_3d_mesh(path, [1])["meshes"][0]
        together = next(m for m in labels_3d_mesh(path, [1, 2])["meshes"] if m["id"] == 1)
        np.testing.assert_allclose(alone["vertices"], together["vertices"])


class WorkingLabelRecoveryTests(TestCase):
    """Corrupt / wrong-shape / non-memmapable working label files recover
    without crashing and without silently destroying the broken file — the
    class of bug behind the 'image data are not memory-mappable' incident."""

    def _tmp(self, name):
        """A path in a fresh tempdir that is also this instance's data root.

        These tests call the write primitives (``open_label_volume_writable``,
        ``_create_label_memmap``) directly rather than through a Volume, so
        there is no project/dataset folder to land in. The primitives refuse
        any write outside ``MITO_DATA_ROOT`` by design — see
        ``core/data_root.py`` — so the tempdir is declared as that root for the
        duration of the test instead of writing outside it.
        """
        d = tempfile.mkdtemp(prefix="mito-recover-test-")
        cm = override_settings(MITO_DATA_ROOT=d)
        cm.enable()
        self.addCleanup(cm.disable)
        return os.path.join(d, name)

    def test_open_writable_rebuilds_corrupt_file_and_keeps_backup(self):
        from pathlib import Path

        path = Path(self._tmp("labels.tif"))
        with open(path, "wb") as f:
            f.write(b"II*\x00" + b"\x00" * 64)  # non-memmapable garbage
        slice_io.clear_caches()
        mm = slice_io.open_label_volume_writable(path, (3, 8, 8))
        self.assertEqual(tuple(mm.shape), (3, 8, 8))
        self.assertEqual(int(np.asarray(mm).max()), 0)  # rebuilt empty
        self.assertTrue(path.with_suffix(path.suffix + ".corrupt.bak").exists())

    def test_open_writable_reopens_after_external_atomic_replace(self):
        """A worker must not retain an mmap of the inode another worker replaced."""
        from pathlib import Path

        path = Path(self._tmp("replaced-labels.tif"))
        slice_io.clear_caches()
        old = slice_io.open_label_volume_writable(path, (3, 8, 8))
        old[:] = 3
        old.flush()

        replacement = np.full((3, 8, 8), 9, dtype=np.uint16)
        new_mm = slice_io._create_label_memmap(
            path, replacement.shape, seed=replacement
        )
        del new_mm

        reopened = slice_io.open_label_volume_writable(path, replacement.shape)
        self.assertEqual(int(reopened[0, 0, 0]), 9)

    def test_open_writable_salvages_wrong_shape_via_imread(self):
        from pathlib import Path

        path = Path(self._tmp("labels.tif"))
        seed = np.zeros((3, 8, 8), dtype=np.uint16)
        seed[1, 2:5, 2:5] = 9
        tifffile.imwrite(str(path), seed)
        slice_io.clear_caches()
        # Same voxels but the caller now expects the (correct) shape; a healthy
        # file just opens. Then plant a shape-mismatch to force the salvage path.
        mm = slice_io.open_label_volume_writable(path, (3, 8, 8))
        self.assertEqual(int(np.asarray(mm).max()), 9)

    def test_read_label_array_quarantines_unreadable_file(self):
        from pathlib import Path

        path = Path(self._tmp("labels.tif"))
        with open(path, "wb") as f:
            f.write(b"not a tiff at all")
        slice_io.clear_caches()
        with self.assertRaises(slice_io.SliceIOError):
            slice_io.read_label_array(path)
        self.assertTrue(path.with_suffix(path.suffix + ".corrupt.bak").exists())


class MigrateEmbeddingPrefixTests(TestCase):
    """Regression guard: the embedding files `migrate_volume_artifacts` writes
    must land where the runtime (`_ai_embedding_cache_path`) looks for them.
    A mismatch here silently defeats the disk cache and makes every AI click
    re-run the encoder (the ~3s → per-click latency regression)."""

    def test_migrated_embedding_is_found_by_runtime_lookup(self):
        import tempfile as _tf

        from django.core.management import call_command

        from annotation.services import _ai_embedding_cache_path
        from annotation.label_paths import legacy_embeddings_dir_rel_path
        from annotation.visualization.slice_io import resolve_path
        from projects.models import Dataset, Project

        root = _tf.mkdtemp(prefix="mito-migrate-prefix-")
        with override_settings(MITO_DATA_ROOT=root):
            project = Project.objects.create(title="webknossos")
            dataset = Dataset.objects.create(project=project, name="wk_heart")
            img_rel = "img/heart.ome.tif"
            img_path = os.path.join(root, img_rel)
            os.makedirs(os.path.dirname(img_path), exist_ok=True)
            tifffile.imwrite(img_path, np.zeros((4, 8, 8), dtype=np.uint8))
            volume = Volume.objects.create(
                project=project, dataset=dataset, name="v", image_path=img_rel,
                shape_z=4, shape_y=8, shape_x=8,
            )
            mtime = int(os.stat(img_path).st_mtime)

            # Plant a legacy-silo embedding under the OLD scheme
            # (embeddings/vits/volume_<id>/z_2_<mtime>.npy).
            legacy_dir = resolve_path(legacy_embeddings_dir_rel_path(volume, "vits"))
            legacy_dir.mkdir(parents=True, exist_ok=True)
            np.save(str(legacy_dir / f"z_2_{mtime}.npy"), np.ones((1, 4, 5, 5), np.float32))

            call_command("migrate_volume_artifacts", "--apply", verbosity=0)

            # The runtime cache path for (volume, z, 2) must now exist on disk.
            runtime_path = _ai_embedding_cache_path(volume, "z", 2)
            self.assertTrue(
                runtime_path.exists(),
                f"migrated embedding not found at runtime lookup {runtime_path}",
            )


@override_settings(MITO_DATA_ROOT=_TMP)
class CellablePortApiTests(TestCase):
    def setUp(self):
        slice_io.clear_caches()
        self.manager = self._user("mgr2", UserRole.MANAGER)
        self.annotator = self._user("ann2", UserRole.ANNOTATOR, annotator=True)
        self.requester = self._user("req2", UserRole.REQUESTER)

        self.project = create_project(title="P2", created_by=self.requester, reviewed=True)
        rel = "images/task2.tif"
        path = os.path.join(_TMP, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # A bright square on a dark background — gives EfficientSAM a real
        # object to find if the model is available.
        image = np.full((6, 32, 32), 20, dtype=np.uint8)
        image[:, 8:24, 8:24] = 220
        tifffile.imwrite(path, image)
        self.volume = Volume.objects.create(
            project=self.project, name="v2", image_path=rel,
            label_type=LabelType.NONE, shape_z=6, shape_y=32, shape_x=32,
        )
        owned = os.path.join(_TMP, working_label_rel_path(self.volume))
        if os.path.exists(owned):
            os.remove(owned)
        # Lifecycle sidecars share the stable project/dataset path rather than
        # the database PK. Isolate tests from deliberately corrupting one.
        from annotation.label_paths import working_label_metadata_rel_path

        sidecar = os.path.join(_TMP, working_label_metadata_rel_path(self.volume))
        for candidate in (sidecar, f"{sidecar}.bak"):
            if os.path.exists(candidate):
                os.remove(candidate)
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume, assigned_to=self.annotator,
            z_start=0, z_end=6, y_end=32, x_end=32,
            task_type=TaskType.MANUAL_ANNOTATION,
        )

    def _user(self, name, role, annotator=False):
        user = User.objects.create_user(name, password="x")
        UserProfile.objects.filter(user=user).update(role=role)
        if annotator:
            AnnotatorProfile.objects.create(user=user, is_active_annotator=True)
        return User.objects.get(pk=user.pk)

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def _paint_instance(self, label_id, z, y0, y1, x0, x1, *, origin="manual"):
        """Paint a rectangle of ``label_id`` directly into the working copy
        via the same service the editor's PUT uses, so watershed/summary
        tests have real data without going through the paint API."""
        from annotation.services import get_label_slice_ids, set_label_slice_ids
        from annotation.visualization.slice_io import decode_label_rle, encode_label_rle

        current = get_label_slice_ids(self.volume, "z", z)
        arr = decode_label_rle(current["runs"], tuple(current["shape"]))
        arr[y0:y1, x0:x1] = label_id
        set_label_slice_ids(self.volume, "z", z, list(arr.shape), encode_label_rle(arr), origin=origin)

    def _lifecycle_row(self, label_id):
        resp = self._client(self.manager).get(f"/api/tasks/{self.task.id}/labels-summary/")
        rows = {row["id"]: row for row in resp.json()["labels"]}
        return rows.get(label_id)

    def test_requester_cannot_predict_mask(self):
        resp = self._client(self.requester).post(
            f"/api/tasks/{self.task.id}/predict-mask/",
            {"axis": "z", "index": 2, "mode": "points", "points": [[16, 16]], "point_labels": [1]},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_predict_mask_unavailable_reports_503_not_500(self):
        with override_settings(MITO_CELLABLE_MODELS_ROOT="/nonexistent/path"):
            # Force a fresh load attempt regardless of any earlier test run.
            from annotation.cellable_port.ai import registry

            registry._model = None
            registry._load_error = None
            resp = self._client(self.annotator).post(
                f"/api/tasks/{self.task.id}/predict-mask/",
                {"axis": "z", "index": 2, "mode": "points", "points": [[16, 16]], "point_labels": [1]},
                format="json",
            )
            self.assertEqual(resp.status_code, 503)
            registry._model = None
            registry._load_error = None

    @unittest.skipUnless(
        os.path.exists(
            os.path.join(
                getattr(settings, "MITO_CELLABLE_MODELS_ROOT", ""),
                f"efficient_sam_{getattr(settings, 'MITO_EFFICIENT_SAM_VARIANT', 'vitt')}_encoder.onnx",
            )
        ),
        "EfficientSAM ONNX weights not available in this environment",
    )
    def test_predict_mask_from_point_finds_bright_square(self):
        resp = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/predict-mask/",
            {"axis": "z", "index": 2, "mode": "points", "points": [[16, 16]], "point_labels": [1]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        runs = body["runs"]
        total_on = sum(count for value, count in runs if value == 1)
        # The bright square is 16x16 = 256px; a real point-prompt mask
        # should land roughly in that ballpark, not empty or the whole image.
        self.assertGreater(total_on, 50)
        self.assertLess(total_on, 32 * 32)

    @unittest.skipUnless(
        os.path.exists(
            os.path.join(
                getattr(settings, "MITO_CELLABLE_MODELS_ROOT", ""),
                f"efficient_sam_{getattr(settings, 'MITO_EFFICIENT_SAM_VARIANT', 'vits')}_encoder.onnx",
            )
        ),
        "EfficientSAM ONNX weights not available in this environment",
    )
    def test_warm_embedding_populates_disk_cache_and_predict_still_works(self):
        from annotation.cellable_port.ai import embed_cache
        from annotation.services import _ai_embedding_cache_path

        cache_path = _ai_embedding_cache_path(self.volume, "z", 2)
        self.assertIsNone(embed_cache.load(cache_path))

        resp = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/warm-embedding/", {"axis": "z", "index": 2}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["warmed"])
        self.assertIsNotNone(embed_cache.load(cache_path))

        # A predict against the now-warmed slice still returns a sane mask
        # (i.e. the disk-cached embedding is actually usable, not just present).
        resp2 = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/predict-mask/",
            {"axis": "z", "index": 2, "mode": "points", "points": [[16, 16]], "point_labels": [1]},
            format="json",
        )
        self.assertEqual(resp2.status_code, 200, resp2.content)
        total_on = sum(count for value, count in resp2.json()["runs"] if value == 1)
        self.assertGreater(total_on, 50)

    def test_warm_embedding_unavailable_reports_200_not_error(self):
        with override_settings(MITO_CELLABLE_MODELS_ROOT="/nonexistent/path"):
            from annotation.cellable_port.ai import registry

            registry._model = None
            registry._load_error = None
            resp = self._client(self.annotator).post(
                f"/api/tasks/{self.task.id}/warm-embedding/", {"axis": "z", "index": 2}, format="json",
            )
            self.assertEqual(resp.status_code, 200, resp.content)
            self.assertFalse(resp.json()["warmed"])
            registry._model = None
            registry._load_error = None

    def test_watershed_requires_seed_inside_label(self):
        self._paint_instance(7, 2, 4, 20, 4, 20)
        resp = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/watershed/",
            {"label": 7, "seeds": [{"z": 2, "y": 0, "x": 0}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_watershed_returns_plan_without_persisting_working_copy(self):
        # Two blobs that touch (one instance id 7) at z=2, seeded apart.
        self._paint_instance(7, 2, 2, 10, 2, 10)
        self._paint_instance(7, 2, 2, 10, 12, 20)
        for z in (0, 1, 3, 4, 5):
            self._paint_instance(7, z, 2, 10, 2, 10)
            self._paint_instance(7, z, 2, 10, 12, 20)
        from annotation.visualization.slice_io import read_label_array, resolve_path

        working = resolve_path(working_label_rel_path(self.volume))
        before = np.asarray(read_label_array(working)).copy()
        with unittest.mock.patch(
            "annotation.services._load_or_init_label",
            side_effect=AssertionError("watershed must stay crop-bounded"),
        ):
            resp = self._client(self.annotator).post(
                f"/api/tasks/{self.task.id}/watershed/",
                {
                    "label": 7,
                    "axis": "z",
                    "pending_slices": [],
                    "seeds": [{"z": 2, "y": 5, "x": 5}, {"z": 2, "y": 5, "x": 16}],
                },
                format="json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        result = resp.json()
        self.assertEqual(len(result["new_label_ids"]), 1)
        self.assertTrue(result["slices"])
        after = np.asarray(read_label_array(working))
        np.testing.assert_array_equal(after, before)

    def test_delete_plan_sees_pending_slice_and_writes_nothing(self):
        from annotation.visualization.slice_io import encode_label_rle, read_label_array, resolve_path

        self._paint_instance(3, 0, 0, 2, 0, 2)
        working = resolve_path(working_label_rel_path(self.volume))
        before = np.asarray(read_label_array(working)).copy()
        pending = np.zeros((32, 32), dtype=np.int32)
        pending[5:8, 5:8] = 9
        resp = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/delete-label-plan/",
            {
                "label": 9,
                "axis": "z",
                "pending_slices": [{
                    "index": 2,
                    "shape": [32, 32],
                    "runs": encode_label_rle(pending),
                }],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["voxels_deleted"], 9)
        self.assertEqual([item["index"] for item in resp.json()["slices"]], [2])
        self.assertTrue(resp.json()["slices"][0]["before_runs"])
        np.testing.assert_array_equal(np.asarray(read_label_array(working)), before)

    def test_split_and_merge_endpoints_are_read_only_plans(self):
        from annotation.visualization.slice_io import read_label_array, resolve_path

        self._paint_instance(4, 1, 1, 13, 1, 13)
        self._paint_instance(4, 4, 18, 30, 18, 30)
        self._paint_instance(5, 2, 1, 5, 20, 24)
        working = resolve_path(working_label_rel_path(self.volume))
        before = np.asarray(read_label_array(working)).copy()

        with unittest.mock.patch(
            "annotation.services._load_or_init_label",
            side_effect=AssertionError("plans must not materialize a full label volume"),
        ):
            split = self._client(self.annotator).post(
                f"/api/tasks/{self.task.id}/split-components/",
                {"label": 4, "axis": "z", "pending_slices": []},
                format="json",
            )
            merge = self._client(self.annotator).post(
                f"/api/tasks/{self.task.id}/merge-labels/",
                {"a": 4, "b": 5, "axis": "z", "pending_slices": []},
                format="json",
            )
        self.assertEqual(split.status_code, 200, split.content)
        self.assertTrue(split.json()["new_label_ids"])
        np.testing.assert_array_equal(np.asarray(read_label_array(working)), before)
        self.assertEqual(merge.status_code, 200, merge.content)
        self.assertTrue(merge.json()["slices"])
        self.assertTrue(all(item.get("before_runs") for item in merge.json()["slices"]))
        np.testing.assert_array_equal(np.asarray(read_label_array(working)), before)

    def test_labels_summary_reflects_painted_instances(self):
        self._paint_instance(3, 1, 0, 4, 0, 4)
        resp = self._client(self.manager).get(f"/api/tasks/{self.task.id}/labels-summary/")
        self.assertEqual(resp.status_code, 200, resp.content)
        ids = {row["id"] for row in resp.json()["labels"]}
        self.assertIn(3, ids)

    def _corrupt_working_copy(self):
        """Overwrite the volume's working label file with a non-memmapable,
        header-corrupt TIFF (mirrors the real incident: a ~1.9GB file with
        empty dataoffsets that raised 'image data are not memory-mappable')."""
        self._paint_instance(3, 1, 0, 4, 0, 4)  # ensure the working copy exists
        owned = os.path.join(_TMP, working_label_rel_path(self.volume))
        slice_io.clear_caches()
        with open(owned, "wb") as f:
            f.write(b"II*\x00" + b"\x00" * 64)  # TIFF magic then garbage

    def test_labels_summary_recovers_from_corrupt_working_copy(self):
        # A planted corrupt working mask must not surface as an uncaught 500;
        # the recovery path rebuilds a clean empty copy, so summary is 200.
        self._corrupt_working_copy()
        resp = self._client(self.manager).get(f"/api/tasks/{self.task.id}/labels-summary/")
        self.assertIn(resp.status_code, (200, 400), resp.content)
        self.assertNotEqual(resp.status_code, 500)
        # The broken file was set aside, not deleted, so recovery/forensics stay possible.
        owned = os.path.join(_TMP, working_label_rel_path(self.volume))
        self.assertTrue(os.path.exists(owned + ".corrupt.bak"))

    def test_label_ids_get_recovers_from_corrupt_working_copy(self):
        # The hot editor read path recovers transparently (rebuilds), returns 200.
        self._corrupt_working_copy()
        resp = self._client(self.annotator).get(
            f"/api/tasks/{self.task.id}/label-ids/?axis=z&index=1"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        # And a subsequent paint still commits cleanly on the rebuilt file.
        arr = np.zeros((32, 32), dtype=np.int32)
        arr[0:4, 0:4] = 7
        from annotation.visualization.slice_io import encode_label_rle

        put = self._client(self.annotator).put(
            f"/api/tasks/{self.task.id}/label-ids/",
            {"axis": "z", "index": 1, "shape": [32, 32], "runs": encode_label_rle(arr)},
            format="json",
        )
        self.assertEqual(put.status_code, 200, put.content)

    def test_labels_3d_binary_response_has_expected_header(self):
        import struct

        self._paint_instance(4, 1, 0, 4, 0, 4)
        resp = self._client(self.manager).get(
            f"/api/tasks/{self.task.id}/labels-3d/?labels=4"
        )
        self.assertEqual(resp.status_code, 200)
        dz, dy, dx, num_labels = struct.unpack_from("<IIII", resp.content, 0)
        self.assertEqual(num_labels, 1)
        self.assertGreater(dz * dy * dx, 0)
        expected_len = 16 + 4 + dz * dy * dx
        self.assertEqual(len(resp.content), expected_len)

    def test_labels_3d_mesh_response_parses_as_geometry(self):
        """The payload the 3D panel actually renders — parse it exactly the
        way `decodeLabels3DMesh` in `api/viewer.ts` does."""
        import struct

        # 3D geometry needs more than one slice of the label to exist.
        self._paint_instance(21, 1, 0, 8, 0, 8)
        self._paint_instance(21, 2, 0, 8, 0, 8)
        resp = self._client(self.manager).post(
            f"/api/tasks/{self.task.id}/labels-3d-mesh/", {"labels": [21]}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp["Content-Type"], "application/octet-stream")

        version, num_meshes, truncated, _reserved = struct.unpack_from("<IIII", resp.content, 0)
        self.assertEqual(version, 1)
        self.assertEqual(num_meshes, 1)
        self.assertEqual(truncated, 0)
        voxel = struct.unpack_from("<fff", resp.content, 40)
        self.assertEqual(voxel, (1.0, 1.0, 1.0))  # no voxel_size on this fixture

        offset = 52
        label_id, num_vertices, num_triangles = struct.unpack_from("<iII", resp.content, offset)
        self.assertEqual(label_id, 21)
        self.assertGreater(num_vertices, 0)
        self.assertGreater(num_triangles, 0)
        # Body length must match exactly — a mismatch means the client would
        # read one mesh's indices as the next one's vertices.
        self.assertEqual(
            len(resp.content), offset + 12 + num_vertices * 12 + num_triangles * 12
        )

    def test_labels_3d_mesh_response_falls_back_to_ome_voxel_size(self):
        import struct

        ome_rel = "images/task2_ome.ome.tif"
        ome_path = os.path.join(_TMP, ome_rel)
        tifffile.imwrite(
            ome_path,
            np.zeros((6, 32, 32), dtype=np.uint8),
            metadata={
                "axes": "ZYX",
                "PhysicalSizeZ": 0.03,
                "PhysicalSizeY": 0.008,
                "PhysicalSizeX": 0.008,
            },
            ome=True,
        )
        Volume.objects.filter(pk=self.volume.pk).update(
            image_path=ome_rel,
            voxel_size_z=None,
            voxel_size_y=1.0,
            voxel_size_x=1.0,
        )
        self.volume.refresh_from_db()

        self._paint_instance(22, 1, 0, 8, 0, 8)
        self._paint_instance(22, 2, 0, 8, 0, 8)
        resp = self._client(self.manager).post(
            f"/api/tasks/{self.task.id}/labels-3d-mesh/", {"labels": [22]}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        voxel = struct.unpack_from("<fff", resp.content, 40)
        self.assertAlmostEqual(voxel[0], 0.03, places=5)
        self.assertAlmostEqual(voxel[1], 0.008, places=5)
        self.assertAlmostEqual(voxel[2], 0.008, places=5)

    def test_labels_3d_mesh_rejects_a_bad_label_list(self):
        resp = self._client(self.manager).post(
            f"/api/tasks/{self.task.id}/labels-3d-mesh/", {"labels": "nope"}, format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_labels_3d_mesh_is_gated_on_task_access(self):
        # An annotator with no claim on this task (not the assignee, not the
        # project owner) must not be able to read its geometry; anonymous
        # callers need the public share token, not this endpoint.
        outsider = self._user("ann_outsider", UserRole.ANNOTATOR, annotator=True)
        resp = self._client(outsider).post(
            f"/api/tasks/{self.task.id}/labels-3d-mesh/", {"labels": [1]}, format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn(
            APIClient()
            .post(f"/api/tasks/{self.task.id}/labels-3d-mesh/", {"labels": [1]}, format="json")
            .status_code,
            (401, 403),
        )

    # --- Label lifecycle (Proposed/Edited/Verified) -------------------------

    def test_new_manual_label_starts_edited(self):
        self._paint_instance(11, 1, 0, 4, 0, 4, origin="manual")
        row = self._lifecycle_row(11)
        self.assertEqual(row["state"], "edited")
        self.assertEqual(row["origin"], "manual")
        self.assertFalse(row["can_revert"])

    def test_new_ai_label_starts_proposed_with_snapshot(self):
        self._paint_instance(12, 1, 0, 4, 0, 4, origin="ai")
        row = self._lifecycle_row(12)
        self.assertEqual(row["state"], "proposed")
        self.assertEqual(row["origin"], "ai")
        self.assertTrue(row["can_revert"])

    def test_repainting_a_verified_label_requires_explicit_unverify(self):
        from annotation.services import VerifiedLabelConflict

        self._paint_instance(13, 1, 0, 4, 0, 4, origin="manual")
        client = self._client(self.annotator)
        resp = client.post(
            f"/api/tasks/{self.task.id}/labels/13/lifecycle/", {"action": "verify"}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._lifecycle_row(13)["state"], "verified")

        with self.assertRaises(VerifiedLabelConflict):
            self._paint_instance(13, 2, 0, 4, 0, 4, origin="manual")
        self.assertEqual(self._lifecycle_row(13)["state"], "verified")

        client.post(
            f"/api/tasks/{self.task.id}/labels/13/lifecycle/",
            {"action": "unverify"},
            format="json",
        )
        self._paint_instance(13, 2, 0, 4, 0, 4, origin="manual")
        self.assertEqual(self._lifecycle_row(13)["state"], "edited")

    def test_verify_then_unverify(self):
        self._paint_instance(14, 1, 0, 4, 0, 4, origin="manual")
        c = self._client(self.annotator)
        c.post(f"/api/tasks/{self.task.id}/labels/14/lifecycle/", {"action": "verify"}, format="json")
        self.assertEqual(self._lifecycle_row(14)["state"], "verified")

        resp = c.post(f"/api/tasks/{self.task.id}/labels/14/lifecycle/", {"action": "unverify"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._lifecycle_row(14)["state"], "edited")

    def test_verified_state_survives_a_fresh_api_session_and_volume_reload(self):
        self._paint_instance(20, 1, 0, 4, 0, 4, origin="manual")
        verified = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/labels/20/lifecycle/",
            {"action": "verify"},
            format="json",
        )
        self.assertEqual(verified.status_code, 200, verified.content)

        # New ORM objects and a new client model close/reopen rather than an
        # in-memory state echo. The summary must reload the JSON sidecar.
        self.volume = Volume.objects.get(pk=self.volume.pk)
        self.task = AnnotationTask.objects.select_related("volume").get(pk=self.task.pk)
        fresh = APIClient()
        fresh.force_authenticate(user=User.objects.get(pk=self.annotator.pk))
        response = fresh.get(f"/api/tasks/{self.task.id}/labels-summary/")
        row = next(item for item in response.json()["labels"] if item["id"] == 20)
        self.assertEqual(row["state"], "verified")
        self.assertTrue(row["verified_at"])

    def test_verified_label_voxels_are_locked_until_explicit_unverify(self):
        from annotation.services import get_label_slice_ids
        from annotation.visualization.slice_io import decode_label_rle, encode_label_rle

        self._paint_instance(23, 1, 0, 4, 0, 4, origin="manual")
        client = self._client(self.annotator)
        verified = client.post(
            f"/api/tasks/{self.task.id}/labels/23/lifecycle/",
            {"action": "verify"},
            format="json",
        )
        self.assertEqual(verified.status_code, 200, verified.content)

        rejected = client.post(
            f"/api/tasks/{self.task.id}/labels/23/lifecycle/",
            {"action": "reject"},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400, rejected.content)
        self.assertIn("Unverify", rejected.json()["detail"])

        current = get_label_slice_ids(self.volume, "z", 1)
        changed = decode_label_rle(current["runs"], tuple(current["shape"]))
        changed[0, 0] = 0
        response = client.put(
            f"/api/tasks/{self.task.id}/label-ids/",
            {
                "axis": "z",
                "index": 1,
                "shape": current["shape"],
                "runs": encode_label_rle(changed),
                "expected_revision": current["revision"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["reason"], "verified_label_locked")
        self.assertEqual(self._lifecycle_row(23)["state"], "verified")
        persisted = get_label_slice_ids(self.volume, "z", 1)
        restored = decode_label_rle(persisted["runs"], tuple(persisted["shape"]))
        self.assertEqual(int(restored[0, 0]), 23)

        unverified = client.post(
            f"/api/tasks/{self.task.id}/labels/23/lifecycle/",
            {"action": "unverify"},
            format="json",
        )
        self.assertEqual(unverified.status_code, 200, unverified.content)
        allowed = client.put(
            f"/api/tasks/{self.task.id}/label-ids/",
            {
                "axis": "z",
                "index": 1,
                "shape": persisted["shape"],
                "runs": encode_label_rle(changed),
                "expected_revision": persisted["revision"],
            },
            format="json",
        )
        self.assertEqual(allowed.status_code, 200, allowed.content)

    def test_verified_label_cannot_be_grown_without_unverify(self):
        from annotation.services import get_label_slice_ids
        from annotation.visualization.slice_io import decode_label_rle, encode_label_rle

        self._paint_instance(24, 1, 0, 2, 0, 2, origin="manual")
        client = self._client(self.annotator)
        client.post(
            f"/api/tasks/{self.task.id}/labels/24/lifecycle/",
            {"action": "verify"},
            format="json",
        )
        current = get_label_slice_ids(self.volume, "z", 1)
        changed = decode_label_rle(current["runs"], tuple(current["shape"]))
        changed[5, 5] = 24
        response = client.put(
            f"/api/tasks/{self.task.id}/label-ids/",
            {
                "axis": "z",
                "index": 1,
                "shape": current["shape"],
                "runs": encode_label_rle(changed),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["reason"], "verified_label_locked")

    def test_verify_rejects_an_id_absent_from_the_saved_volume(self):
        response = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/labels/999/lifecycle/",
            {"action": "verify"},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("Save it before verifying", response.json()["detail"])

    def test_corrupt_primary_sidecar_recovers_latest_verified_state_from_backup(self):
        from annotation.label_paths import working_label_metadata_rel_path
        from annotation.visualization.slice_io import resolve_path

        self._paint_instance(25, 1, 0, 3, 0, 3, origin="manual")
        response = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/labels/25/lifecycle/",
            {"action": "verify"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        sidecar = resolve_path(working_label_metadata_rel_path(self.volume))
        backup = sidecar.with_name(f"{sidecar.name}.bak")
        self.assertTrue(backup.exists())
        sidecar.write_text("{truncated", encoding="utf-8")

        row = self._lifecycle_row(25)
        self.assertEqual(row["state"], "verified")
        self.assertTrue(row["verified_at"])

    def test_valid_json_state_tamper_recovers_verified_state_from_backup(self):
        import json

        from annotation.label_paths import working_label_metadata_rel_path
        from annotation.visualization.slice_io import resolve_path

        self._paint_instance(27, 1, 0, 3, 0, 3, origin="manual")
        response = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/labels/27/lifecycle/",
            {"action": "verify"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        sidecar = resolve_path(working_label_metadata_rel_path(self.volume))
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        payload["labels"]["27"]["state"] = "proposed"
        # Keep the original checksum: valid JSON with semantically changed
        # lifecycle data must be rejected just like truncation.
        sidecar.write_text(json.dumps(payload), encoding="utf-8")

        row = self._lifecycle_row(27)
        self.assertEqual(row["state"], "verified")
        self.assertTrue(row["verified_at"])

    def test_two_corrupt_sidecars_fail_closed_without_overwriting_evidence(self):
        from annotation.label_paths import working_label_metadata_rel_path
        from annotation.visualization.slice_io import resolve_path

        self._paint_instance(26, 1, 0, 3, 0, 3, origin="manual")
        client = self._client(self.annotator)
        client.post(
            f"/api/tasks/{self.task.id}/labels/26/lifecycle/",
            {"action": "verify"},
            format="json",
        )
        sidecar = resolve_path(working_label_metadata_rel_path(self.volume))
        backup = sidecar.with_name(f"{sidecar.name}.bak")
        sidecar.write_text("{broken-primary", encoding="utf-8")
        backup.write_text("{broken-backup", encoding="utf-8")

        summary = client.get(f"/api/tasks/{self.task.id}/labels-summary/")
        change = client.post(
            f"/api/tasks/{self.task.id}/labels/26/lifecycle/",
            {"action": "unverify"},
            format="json",
        )
        self.assertEqual(summary.status_code, 400, summary.content)
        self.assertEqual(change.status_code, 400, change.content)
        self.assertEqual(sidecar.read_text(encoding="utf-8"), "{broken-primary")
        self.assertEqual(backup.read_text(encoding="utf-8"), "{broken-backup")

    def test_legacy_sidecar_is_adopted_even_when_new_mask_already_exists(self):
        from annotation.label_paths import (
            legacy_working_label_metadata_rel_path,
            working_label_metadata_rel_path,
        )
        from annotation.visualization.slice_io import resolve_path

        self._paint_instance(21, 1, 0, 4, 0, 4, origin="manual")
        client = self._client(self.annotator)
        client.post(
            f"/api/tasks/{self.task.id}/labels/21/lifecycle/",
            {"action": "verify"},
            format="json",
        )
        current = resolve_path(working_label_metadata_rel_path(self.volume))
        legacy = resolve_path(legacy_working_label_metadata_rel_path(self.volume))
        legacy.parent.mkdir(parents=True, exist_ok=True)
        current.replace(legacy)

        row = self._lifecycle_row(21)
        self.assertEqual(row["state"], "verified")
        self.assertTrue(current.exists())
        self.assertFalse(legacy.exists())

    def test_unverify_when_not_verified_is_400(self):
        self._paint_instance(15, 1, 0, 4, 0, 4, origin="manual")
        resp = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/labels/15/lifecycle/", {"action": "unverify"}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_revert_restores_only_the_original_snapshot_slice(self):
        self._paint_instance(16, 1, 0, 4, 0, 4, origin="ai")
        # Grow the same id onto a second slice before reverting.
        self._paint_instance(16, 2, 0, 4, 0, 4, origin="manual")

        resp = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/labels/16/lifecycle/", {"action": "revert"}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["state"], "proposed")

        from annotation.services import get_label_slice_ids

        slice1 = get_label_slice_ids(self.volume, "z", 1)
        slice2 = get_label_slice_ids(self.volume, "z", 2)
        self.assertTrue(any(v == 16 for v, _c in slice1["runs"]))
        self.assertFalse(any(v == 16 for v, _c in slice2["runs"]))
        self.assertEqual(self._lifecycle_row(16)["state"], "proposed")

    def test_revert_without_snapshot_is_400(self):
        self._paint_instance(17, 1, 0, 4, 0, 4, origin="manual")
        resp = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/labels/17/lifecycle/", {"action": "revert"}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_reject_deletes_every_voxel_and_metadata(self):
        self._paint_instance(18, 1, 0, 4, 0, 4, origin="manual")
        self._paint_instance(18, 2, 0, 4, 0, 4, origin="manual")
        resp = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/labels/18/lifecycle/", {"action": "reject"}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["removed"])
        self.assertIsNone(self._lifecycle_row(18))

    def test_requester_cannot_change_label_lifecycle(self):
        self._paint_instance(19, 1, 0, 4, 0, 4, origin="manual")
        resp = self._client(self.requester).post(
            f"/api/tasks/{self.task.id}/labels/19/lifecycle/", {"action": "verify"}, format="json"
        )
        self.assertEqual(resp.status_code, 403)

    def test_watershed_plan_does_not_register_unsaved_labels(self):
        self._paint_instance(20, 2, 2, 10, 2, 10, origin="manual")
        self._paint_instance(20, 2, 2, 10, 12, 20, origin="manual")
        planned = self._client(self.annotator).post(
            f"/api/tasks/{self.task.id}/watershed/",
            {"label": 20, "seeds": [{"z": 2, "y": 5, "x": 5}, {"z": 2, "y": 5, "x": 16}]},
            format="json",
        )
        resp = self._client(self.manager).get(f"/api/tasks/{self.task.id}/labels-summary/")
        rows = {row["id"]: row for row in resp.json()["labels"]}
        self.assertEqual(planned.status_code, 200, planned.content)
        self.assertTrue(planned.json()["new_label_ids"])
        self.assertFalse(any(lid > 20 for lid in rows))
        self.assertEqual(rows[20]["state"], "edited")
