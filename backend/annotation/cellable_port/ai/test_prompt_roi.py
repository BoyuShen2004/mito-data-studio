import numpy as np
from django.test import SimpleTestCase

from .prompt_roi import RoiWindow, compute_prompt_roi, encode_roi_bool_rle


class PromptRoiTests(SimpleTestCase):
    def test_small_plane_uses_full_frame(self):
        self.assertTrue(
            compute_prompt_roi(512, 640, points=[[100, 120]]).covers(512, 640)
        )

    def test_large_plane_is_cropped_snapped_and_contains_prompt(self):
        roi = compute_prompt_roi(3885, 4544, points=[[2200, 1800]])
        self.assertEqual(roi.shape, (1024, 1024))
        self.assertEqual(roi.y0 % 64, 0)
        self.assertEqual(roi.x0 % 64, 0)
        self.assertTrue(roi.y0 <= 1800 < roi.y1)
        self.assertTrue(roi.x0 <= 2200 < roi.x1)

    def test_hover_inside_anchor_reuses_embedding_identity(self):
        first = [2200, 1800]
        base = compute_prompt_roi(3885, 4544, points=[first])
        for tip in ([2220, 1810], [2450, 1950], [2000, 1600]):
            self.assertEqual(
                compute_prompt_roi(3885, 4544, points=[first, tip]), base
            )

    def test_large_prompt_extent_is_bounded(self):
        roi = compute_prompt_roi(
            4000,
            5000,
            points=[[100, 100], [1500, 900]],
            max_size=2048,
        )
        self.assertLessEqual(roi.height, 2048)
        self.assertLessEqual(roi.width, 2048)
        self.assertTrue(roi.x0 <= 100 < roi.x1)
        self.assertTrue(roi.x0 <= 1500 < roi.x1)

    def test_crop_rle_decodes_to_exact_full_plane(self):
        roi = RoiWindow(2, 5, 3, 7)
        crop = np.array(
            [[0, 1, 1, 0], [1, 1, 0, 0], [0, 0, 0, 1]], dtype=bool
        )
        runs = encode_roi_bool_rle((8, 10), roi, crop)
        decoded = np.concatenate(
            [np.full(count, value, dtype=bool) for value, count in runs]
        ).reshape(8, 10)
        expected = np.zeros((8, 10), dtype=bool)
        expected[2:5, 3:7] = crop
        np.testing.assert_array_equal(decoded, expected)

    def test_rejects_mask_shape_mismatch(self):
        with self.assertRaises(ValueError):
            encode_roi_bool_rle(
                (10, 10), RoiWindow(1, 4, 1, 4), np.zeros((2, 2), dtype=bool)
            )
