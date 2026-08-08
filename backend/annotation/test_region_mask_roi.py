import tempfile
from pathlib import Path

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from annotation.services import get_label_slice_ids, set_label_slice_ids
from annotation.visualization.slice_io import decode_label_rle, encode_label_rle
from projects.models import Dataset, Project
from volumes.models import Volume


class RoiOnlyWriteTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.override = override_settings(MITO_DATA_ROOT=self.root)
        self.override.enable()
        user = get_user_model().objects.create_user("roi-owner")
        project = Project.objects.create(title="ROI project", created_by=user)
        dataset = Dataset.objects.create(project=project, name="ROI data")
        tifffile.imwrite(self.root / "image.tif", np.zeros((1, 4, 4), dtype=np.uint8))
        mask = np.zeros((1, 4, 4), dtype=np.uint8)
        mask[0, :2, :2] = 1
        tifffile.imwrite(self.root / "roi.tif", mask)
        self.volume = Volume.objects.create(
            project=project,
            dataset=dataset,
            name="ROI volume",
            image_path="image.tif",
            region_mask_path="roi.tif",
            shape_z=1,
            shape_y=4,
            shape_x=4,
        )

    def tearDown(self):
        self.override.disable()
        self.tmp.cleanup()

    def test_paint_outside_roi_is_preserved_while_inside_is_written(self):
        proposed = np.full((4, 4), 7, dtype=np.int32)
        set_label_slice_ids(
            self.volume,
            "z",
            0,
            [4, 4],
            encode_label_rle(proposed),
            roi_only=True,
        )
        saved = get_label_slice_ids(self.volume, "z", 0)
        labels = decode_label_rle(saved["runs"], (4, 4))
        expected = np.zeros((4, 4), dtype=np.int32)
        expected[:2, :2] = 7
        np.testing.assert_array_equal(labels, expected)

    def test_roi_only_rejected_when_volume_has_no_region_mask(self):
        self.volume.region_mask_path = ""
        self.volume.save(update_fields=["region_mask_path"])
        with self.assertRaisesMessage(ValueError, "requires a region mask"):
            set_label_slice_ids(
                self.volume,
                "z",
                0,
                [4, 4],
                encode_label_rle(np.ones((4, 4), dtype=np.int32)),
                roi_only=True,
            )

    def test_an_empty_roi_writes_nothing_without_reading_the_mask(self):
        """Coverage 0 means no voxel can be written, so the read is pointless.

        Asserted through the reader itself rather than by timing: a future
        change that reintroduces the read fails here instead of getting slower
        somewhere nobody measures.
        """
        from unittest.mock import patch

        self.volume.region_mask_coverage = 0.0
        self.volume.save(update_fields=["region_mask_coverage"])
        with patch(
            "annotation.visualization.slice_io.read_slice",
            side_effect=AssertionError("the empty ROI was read"),
        ) as reader:
            set_label_slice_ids(
                self.volume,
                "z",
                0,
                [4, 4],
                encode_label_rle(np.full((4, 4), 7, dtype=np.int32)),
                roi_only=True,
            )
        self.assertFalse(reader.called)
        saved = get_label_slice_ids(self.volume, "z", 0)
        labels = decode_label_rle(saved["runs"], (4, 4))
        np.testing.assert_array_equal(labels, np.zeros((4, 4), dtype=np.int32))

    def test_unmeasured_coverage_still_reads_the_mask(self):
        """`None` is "not measured", which must never be treated as empty."""
        self.assertIsNone(self.volume.region_mask_coverage)
        set_label_slice_ids(
            self.volume,
            "z",
            0,
            [4, 4],
            encode_label_rle(np.full((4, 4), 5, dtype=np.int32)),
            roi_only=True,
        )
        saved = get_label_slice_ids(self.volume, "z", 0)
        labels = decode_label_rle(saved["runs"], (4, 4))
        expected = np.zeros((4, 4), dtype=np.int32)
        expected[:2, :2] = 5
        np.testing.assert_array_equal(labels, expected)


class VolumeWideRegionMembershipTests(TestCase):
    """"Region only" is a whole-instance decision, so membership is volume-wide.

    The bug these pin: deciding per plane hid a mitochondrion on every layer
    where that same id happened not to reach the ROI, even though it clearly
    does elsewhere — one object rendered as a handful of disconnected fragments.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.override = override_settings(MITO_DATA_ROOT=self.root)
        self.override.enable()
        user = get_user_model().objects.create_user("roi-membership-owner")
        project = Project.objects.create(title="ROI 3D project", created_by=user)
        dataset = Dataset.objects.create(project=project, name="ROI 3D data")
        tifffile.imwrite(self.root / "image3d.tif", np.zeros((4, 4, 4), dtype=np.uint8))
        # The ROI is a 2x2 column in the top-left corner, on plane z=0 only.
        mask = np.zeros((4, 4, 4), dtype=np.uint8)
        mask[0, :2, :2] = 1
        tifffile.imwrite(self.root / "roi3d.tif", mask)
        # Label 7 enters the ROI on z=0 and continues outside it on z=1..3.
        # Label 8 never touches the ROI on any plane.
        labels = np.zeros((4, 4, 4), dtype=np.uint16)
        labels[0, 0, 0] = 7
        labels[1:, 3, 3] = 7
        labels[:, 2, 2] = 8
        tifffile.imwrite(self.root / "labels3d.tif", labels)
        self.volume = Volume.objects.create(
            project=project,
            dataset=dataset,
            name="ROI 3D volume",
            image_path="image3d.tif",
            label_path="labels3d.tif",
            region_mask_path="roi3d.tif",
            shape_z=4,
            shape_y=4,
            shape_x=4,
        )

    def tearDown(self):
        self.override.disable()
        self.tmp.cleanup()

    def _ids(self):
        from annotation.services import get_region_label_ids

        return get_region_label_ids(self.volume)

    def test_a_label_touching_the_roi_on_one_plane_is_a_member(self):
        payload = self._ids()
        self.assertTrue(payload["has_region"])
        # 7 qualifies from its single voxel on z=0; 8 never qualifies.
        self.assertEqual(payload["ids"], [7])

    def test_no_region_mask_reports_no_filtering_rather_than_no_members(self):
        self.volume.region_mask_path = ""
        self.volume.save(update_fields=["region_mask_path"])
        payload = self._ids()
        self.assertFalse(payload["has_region"])
        self.assertEqual(payload["ids"], [])

    def test_an_empty_roi_has_no_members(self):
        tifffile.imwrite(self.root / "roi3d.tif", np.zeros((4, 4, 4), dtype=np.uint8))
        from annotation.region_mask import _roi_bbox_cache

        _roi_bbox_cache.clear()
        payload = self._ids()
        self.assertTrue(payload["has_region"])
        self.assertEqual(payload["ids"], [])

    def test_repeat_calls_are_served_from_cache_without_rescanning(self):
        """Scrubbing must not pay for a volume scan per plane."""
        from unittest.mock import patch

        self.assertEqual(self._ids()["ids"], [7])
        with patch(
            "annotation.visualization.slice_io.open_label_volume_readonly",
            side_effect=AssertionError("the label volume was rescanned"),
        ) as opener:
            self.assertEqual(self._ids()["ids"], [7])
        self.assertFalse(opener.called)

    def test_a_shape_mismatch_is_reported_rather_than_silently_filtering(self):
        tifffile.imwrite(self.root / "roi3d.tif", np.ones((2, 4, 4), dtype=np.uint8))
        from annotation.region_mask import _roi_bbox_cache, forget_region_label_ids

        _roi_bbox_cache.clear()
        forget_region_label_ids()
        with self.assertRaisesMessage(ValueError, "does not match label shape"):
            self._ids()
