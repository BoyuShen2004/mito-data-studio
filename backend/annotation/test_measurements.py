import io
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import tifffile
from django.core.management import call_command
from django.test import SimpleTestCase

from annotation.measurements import (
    SKIPPED_CROP_LIMIT,
    MeasurementError,
    MitoMeasurement,
    measure_label_volume,
    skeleton_length_nm,
    validate_voxel_size,
)
from annotation.visualization import slice_io

VOXEL_ZYX = (30, 16, 16)
COMMAND = "annotation.management.commands.measure_mito"


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

    def _measure(self, labels, **kwargs):
        with tempfile.TemporaryDirectory(prefix="mito-measure-") as root:
            path = Path(root) / "labels.tif"
            tifffile.imwrite(path, labels)
            return measure_label_volume(path, VOXEL_ZYX, **kwargs)

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

    def test_label_over_crop_limit_is_skipped_and_others_still_measured(self):
        labels = np.zeros((20, 20, 120), dtype=np.uint16)
        labels[7:13, 7:13, 10:110] = 5    # padded crop 10 x 10 x 104 = 10,400
        labels[14:18, 14:18, 40:60] = 7   # padded crop 8 x 8 x 24 = 1,536

        tube, blob = self._measure(labels, max_crop_voxels=5000)

        self.assertEqual(tube.label_id, 5)
        self.assertEqual(tube.skipped_reason, SKIPPED_CROP_LIMIT)
        self.assertIsNone(tube.skeleton_length_um)
        self.assertEqual(tube.voxel_count, 3600)
        self.assertAlmostEqual(tube.volume_um3, 3600 * 30 * 16 * 16 / 1e9)
        self.assertFalse(tube.touches_edge)
        self.assertEqual(blob.label_id, 7)
        self.assertIsNone(blob.skipped_reason)
        self.assertIsNotNone(blob.skeleton_length_um)
        self.assertEqual(blob.voxel_count, 320)

    def test_crop_limit_allows_a_crop_exactly_at_the_limit(self):
        labels = np.zeros((20, 20, 120), dtype=np.uint16)
        labels[7:13, 7:13, 10:110] = 5
        with self.subTest(max_crop_voxels=10399):
            (tube,) = self._measure(labels, max_crop_voxels=10399)
            self.assertEqual(tube.skipped_reason, SKIPPED_CROP_LIMIT)
        with self.subTest(max_crop_voxels=10400):
            (tube,) = self._measure(labels, max_crop_voxels=10400)
            self.assertIsNone(tube.skipped_reason)
            self.assertGreater(tube.skeleton_length_um, 1.5)
            self.assertLess(tube.skeleton_length_um, 1.8)

    def test_touches_edge_flags_each_face_and_not_near_misses(self):
        labels = np.zeros((20, 20, 40), dtype=np.uint16)
        labels[0:2, 8:10, 8:10] = 1      # low z face
        labels[18:20, 8:10, 8:10] = 2    # high z face
        labels[8:10, 0:2, 20:22] = 3     # low y face
        labels[8:10, 18:20, 20:22] = 4   # high y face
        labels[4:6, 4:6, 0:2] = 5        # low x face
        labels[14:16, 14:16, 38:40] = 6  # high x face
        labels[8:10, 8:10, 28:30] = 7    # interior
        labels[8:10, 8:10, 37:39] = 8    # one voxel short of high x face
        labels[8:10, 8:10, 1:3] = 9      # one voxel in from low x face

        results = self._measure(labels)

        self.assertEqual(
            {r.label_id: r.touches_edge for r in results},
            {1: True, 2: True, 3: True, 4: True, 5: True, 6: True, 7: False, 8: False, 9: False},
        )
        self.assertTrue(all(r.skipped_reason is None for r in results))
        self.assertTrue(all(r.skeleton_length_um is not None for r in results))


class MeasureMitoCommandTests(SimpleTestCase):
    ROWS = [
        MitoMeasurement(5, 3600, 0.027648, 1.6, touches_edge=False),
        MitoMeasurement(6, 40000000, 307.2, None, touches_edge=True, skipped_reason=SKIPPED_CROP_LIMIT),
        MitoMeasurement(7, 5000, 0.0384, 2.25, touches_edge=True),
    ]

    def test_csv_has_skip_and_edge_columns_and_spacing_is_converted_once(self):
        volume = SimpleNamespace(pk=38, has_label=True, label_location="labels.tif")
        label_path = Path("/data/labels.tif")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch(f"{COMMAND}.Volume") as volume_model, \
                mock.patch(f"{COMMAND}.measurement_spacing", return_value={"voxel_size_um_zyx": (0.03, 0.008, 0.008)}), \
                mock.patch(f"{COMMAND}.resolve_path", return_value=label_path), \
                mock.patch(f"{COMMAND}.measure_label_volume", return_value=self.ROWS) as measure:
            volume_model.objects.get.return_value = volume
            call_command("measure_mito", "--volume", "38", stdout=out, stderr=err)

        path_arg, voxel_arg = measure.call_args.args
        self.assertEqual(path_arg, label_path)
        self.assertEqual([round(v, 6) for v in voxel_arg], [30, 8, 8])
        self.assertEqual(out.getvalue().splitlines(), [
            "label_id,voxel_count,volume_um3,skeleton_length_um,touches_edge,skipped_reason",
            "5,3600,0.027648,1.6,false,",
            "6,40000000,307.2,,true,crop_limit",
            "7,5000,0.0384,2.25,true,",
        ])
        self.assertIn("measured 3 label(s) in volume 38", err.getvalue())
