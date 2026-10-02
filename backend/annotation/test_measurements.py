import tempfile
from pathlib import Path

import numpy as np
import tifffile
from django.test import SimpleTestCase

from annotation.measurements import (
    MeasurementError,
    measure_label_volume,
    skeleton_length_nm,
    validate_voxel_size,
)
from annotation.visualization import slice_io

VOXEL_ZYX = (30, 16, 16)


def tube_along_x(length_voxels=100):
    mask = np.zeros((20, 20, length_voxels + 20), dtype=bool)
    mask[7:13, 7:13, 10 : 10 + length_voxels] = True
    return mask


class SkeletonLengthTests(SimpleTestCase):
    def test_tube_length_matches_physical_extent(self):
        length = skeleton_length_nm(tube_along_x(), VOXEL_ZYX)
        self.assertGreater(length, 1500)
        self.assertLess(length, 1800)

    def test_voxel_size_is_applied_once(self):
        length = skeleton_length_nm(tube_along_x(), VOXEL_ZYX)
        physical_extent = 100 * VOXEL_ZYX[2]
        self.assertLess(length, 2 * physical_extent)

    def test_axis_order_is_zyx(self):
        mask = np.zeros((70, 20, 20), dtype=bool)
        mask[10:60, 7:13, 7:13] = True
        length = skeleton_length_nm(mask, VOXEL_ZYX)
        self.assertGreater(length, 1300)
        self.assertLess(length, 1800)

    def test_empty_and_dust_masks_have_zero_length(self):
        empty = np.zeros((10, 10, 10), dtype=bool)
        self.assertEqual(skeleton_length_nm(empty, VOXEL_ZYX), 0.0)
        dust = np.zeros((10, 10, 10), dtype=bool)
        dust[1:3, 1:3, 1:3] = True
        self.assertEqual(skeleton_length_nm(dust, VOXEL_ZYX), 0.0)

    def test_invalid_voxel_size_is_rejected(self):
        for bad in (None, (30, None, 16), (0, 16, 16), (30, -16, 16), (30, 16)):
            with self.subTest(voxel_size=bad):
                with self.assertRaises(MeasurementError):
                    validate_voxel_size(bad)


class MeasureLabelVolumeTests(SimpleTestCase):
    def tearDown(self):
        slice_io.clear_caches()

    def test_measure_label_volume_from_tiff(self):
        with tempfile.TemporaryDirectory(prefix="mito-measure-") as root:
            path = Path(root) / "labels.tif"
            labels = np.zeros((20, 20, 120), dtype=np.uint16)
            labels[7:13, 7:13, 10:110] = 5
            labels[1:3, 1:3, 1:3] = 9
            tifffile.imwrite(path, labels)

            results = measure_label_volume(path, VOXEL_ZYX)

        self.assertEqual([r.label_id for r in results], [5, 9])
        tube, dust = results
        self.assertEqual(tube.voxel_count, 3600)
        self.assertAlmostEqual(tube.volume_um3, 3600 * 30 * 16 * 16 / 1e9)
        self.assertGreater(tube.skeleton_length_um, 1.5)
        self.assertLess(tube.skeleton_length_um, 1.8)
        self.assertEqual(dust.voxel_count, 8)
        self.assertEqual(dust.skeleton_length_um, 0.0)

    def test_missing_label_file_raises(self):
        with self.assertRaises(MeasurementError):
            measure_label_volume(Path("/nonexistent/labels.tif"), VOXEL_ZYX)
