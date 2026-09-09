"""The cross-worker on-disk cache of SAM 2 encoder features."""

from __future__ import annotations

from pathlib import Path
import tempfile

import numpy as np
from django.test import SimpleTestCase, override_settings

from annotation.cellable_port.ai import sam2_feature_cache as fc


def _payload():
    return {
        "image_embed": np.arange(2 * 3, dtype=np.float16).reshape(1, 2, 3),
        "high_res_feats": [
            np.ones((1, 2, 2), dtype=np.float16),
            np.full((1, 3, 3), 0.5, dtype=np.float16),
        ],
        "orig_hw": [(256, 300)],
    }


class FeatureCacheFileTests(SimpleTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "slice.npz"

    def test_round_trip_preserves_every_level_and_the_original_shape(self):
        fc.save(self.path, _payload())
        got = fc.load(self.path)

        np.testing.assert_array_equal(got["image_embed"], _payload()["image_embed"])
        self.assertEqual(len(got["high_res_feats"]), 2)
        np.testing.assert_array_equal(got["high_res_feats"][0], np.ones((1, 2, 2)))
        np.testing.assert_array_equal(got["high_res_feats"][1], np.full((1, 3, 3), 0.5))
        self.assertEqual(got["orig_hw"], [(256, 300)])

    def test_high_res_levels_keep_their_order(self):
        # They are consumed positionally by the decoder, so a dict iteration
        # that reordered them would be silently wrong, not loudly broken.
        payload = _payload()
        payload["high_res_feats"] = [
            np.full((1, 2, 2), float(level), dtype=np.float16) for level in range(3)
        ]
        fc.save(self.path, payload)

        got = fc.load(self.path)

        self.assertEqual([float(f.flat[0]) for f in got["high_res_feats"]], [0.0, 1.0, 2.0])

    def test_a_missing_file_is_a_miss(self):
        self.assertIsNone(fc.load(self.path))

    def test_a_truncated_file_is_a_miss_not_a_crash(self):
        fc.save(self.path, _payload())
        data = self.path.read_bytes()
        self.path.write_bytes(data[: len(data) // 2])

        self.assertIsNone(fc.load(self.path))

    def test_a_file_missing_expected_arrays_is_a_miss(self):
        np.savez(str(self.path), something_else=np.zeros(3))
        self.assertIsNone(fc.load(self.path))

    def test_saving_leaves_no_temp_file_behind(self):
        fc.save(self.path, _payload())
        self.assertEqual([p.name for p in Path(self.dir.name).iterdir()], ["slice.npz"])

    def test_an_unwritable_directory_does_not_raise(self):
        # A full or read-only disk must not break interactive segmentation.
        blocked = Path(self.dir.name) / "ro" / "slice.npz"
        blocked.parent.mkdir()
        blocked.parent.chmod(0o500)
        self.addCleanup(blocked.parent.chmod, 0o700)

        fc.save(blocked, _payload())  # must not raise

        self.assertIsNone(fc.load(blocked))


class FeatureCachePathTests(SimpleTestCase):
    @override_settings(MITO_DATA_ROOT="/data")
    def test_the_path_carries_volume_axis_index_and_mtime(self):
        p = fc.cache_path_for("P/ds/embeddings", "vol_mask", "z", 12, 1788961524.7)

        self.assertEqual(
            p, Path("/data/P/ds/embeddings/sam2-hiera-l/vol_mask_z_12_1788961524.npz")
        )

    @override_settings(MITO_DATA_ROOT="/data")
    def test_a_new_image_mtime_is_a_different_path(self):
        old = fc.cache_path_for("e", "v", "z", 1, 1000.0)
        new = fc.cache_path_for("e", "v", "z", 1, 2000.0)

        # Invalidation is by unreachability: the stale file is orphaned, never
        # loaded and mistaken for the new image's features.
        self.assertNotEqual(old, new)

    @override_settings(MITO_DATA_ROOT="/data")
    def test_an_roi_token_is_sanitised_into_the_filename(self):
        p = fc.cache_path_for("e", "v", "z", 1, 10.0, roi_token="y0-64_x0-64")

        self.assertTrue(p.name.endswith("_y0-64_x0-64.npz"))
        self.assertNotIn("/", p.name)

    @override_settings(MITO_DATA_ROOT="/data")
    def test_axes_do_not_collide(self):
        self.assertNotEqual(
            fc.cache_path_for("e", "v", "z", 1, 10.0),
            fc.cache_path_for("e", "v", "y", 1, 10.0),
        )


class DiskFeatureStoreTests(SimpleTestCase):
    def test_the_store_round_trips_through_a_path_key(self):
        with tempfile.TemporaryDirectory() as d:
            store = fc.DiskFeatureStore()
            key = str(Path(d) / "k.npz")

            self.assertIsNone(store.load(key))
            store.save(key, _payload())

            self.assertIsNotNone(store.load(key))

    def test_the_compute_lock_is_reentrant_per_process_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as d:
            store = fc.DiskFeatureStore()
            key = str(Path(d) / "k.npz")
            with store.compute_lock(key):
                pass
            with store.compute_lock(key):  # must not deadlock on a second pass
                pass


class PlaneShapeTests(SimpleTestCase):
    """Warming resolves a slice's cache key without decoding the image."""

    class _Vol:
        def __init__(self, z, y, x):
            self.shape_z, self.shape_y, self.shape_x = z, y, x

    def test_each_axis_matches_read_slice_orientation(self):
        from annotation.cellable_port.ai.application import _plane_shape

        vol = self._Vol(64, 256, 300)
        # z -> arr[i] is (y, x); y -> arr[:, i, :] is (z, x); x -> (z, y).
        self.assertEqual(_plane_shape(vol, "z"), (256, 300))
        self.assertEqual(_plane_shape(vol, "y"), (64, 300))
        self.assertEqual(_plane_shape(vol, "x"), (64, 256))

    def test_a_volume_with_no_recorded_shape_falls_back(self):
        from annotation.cellable_port.ai.application import _plane_shape

        # None means "read the image instead", never a guessed shape that
        # would key the cache wrongly.
        self.assertIsNone(_plane_shape(self._Vol(64, None, 300), "z"))
        self.assertIsNone(_plane_shape(self._Vol(0, 256, 300), "y"))
        self.assertIsNone(_plane_shape(self._Vol(64, 256, 300), "w"))
