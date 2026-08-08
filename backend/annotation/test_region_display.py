"""Region-aware *display*: which planes hold ROI, and whole-instance masking.

Both features here are read-only viewer aids. The write-side ROI guards are
covered by `test_region_mask_roi.py`, and the last test in this file asserts
that nothing added here touches the label or region files on disk.
"""

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from annotation.visualization.slice_io import (
    clear_caches,
    labels_touching_region,
    render_label_slice_png,
)
from projects.models import Dataset, Project
from volumes.models import Volume
from volumes.region_masks import (
    calculate_region_nonempty_indices,
    clear_region_index_cache,
    region_nonempty_indices,
)


class _RegionVolumeMixin:
    """A 4-plane volume whose ROI lives on z=1 and z=2 only.

    Labels: id 3 straddles the ROI edge on z=1 (partially inside), id 4 sits
    entirely outside it, id 5 is entirely inside.
    """

    def build_volume(self, *, owner_name="region-display"):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.override = override_settings(MITO_DATA_ROOT=self.root)
        self.override.enable()
        clear_region_index_cache()
        clear_caches()
        user = get_user_model().objects.create_user(owner_name)
        project = Project.objects.create(title="Region display", created_by=user)
        dataset = Dataset.objects.create(project=project, name="Region data")

        tifffile.imwrite(self.root / "image.tif", np.zeros((4, 6, 6), dtype=np.uint8))

        region = np.zeros((4, 6, 6), dtype=np.uint8)
        region[1:3, 2:5, 1:4] = 1
        self.region_path = self.root / "roi.tif"
        tifffile.imwrite(self.region_path, region)

        label = np.zeros((4, 6, 6), dtype=np.uint16)
        label[1, 1:3, 1:3] = 3  # crosses the ROI's row-2 boundary
        label[1, 0, 4:6] = 4  # nowhere near the ROI
        label[1, 3, 2] = 5  # entirely inside
        self.label_path = self.root / "label.tif"
        tifffile.imwrite(self.label_path, label)

        return Volume.objects.create(
            project=project,
            dataset=dataset,
            name="Region volume",
            image_path="image.tif",
            region_mask_path="roi.tif",
            label_path="label.tif",
            shape_z=4,
            shape_y=6,
            shape_x=6,
        )

    def tear_down_volume(self):
        clear_region_index_cache()
        clear_caches()
        self.override.disable()
        self.tmp.cleanup()


class RegionNonemptyIndexTests(_RegionVolumeMixin, TestCase):
    def setUp(self):
        self.volume = self.build_volume()

    def tearDown(self):
        self.tear_down_volume()

    def test_reports_the_planes_that_hold_region_on_every_axis(self):
        indices = calculate_region_nonempty_indices("roi.tif")
        self.assertEqual(indices["z"], [1, 2])
        self.assertEqual(indices["y"], [2, 3, 4])
        self.assertEqual(indices["x"], [1, 2, 3])

    def test_slab_boundaries_do_not_change_the_answer(self):
        """A chunked scan must agree with a single-slab one, plane for plane."""
        self.assertEqual(
            calculate_region_nonempty_indices("roi.tif", chunk_depth=1),
            calculate_region_nonempty_indices("roi.tif", chunk_depth=64),
        )

    def test_second_call_is_served_from_the_memo_without_rereading(self):
        self.assertEqual(region_nonempty_indices(self.volume, "z"), [1, 2])
        with patch(
            "volumes.region_masks.calculate_region_nonempty_indices",
            side_effect=AssertionError("rescanned an already-scanned mask"),
        ) as scan:
            self.assertEqual(region_nonempty_indices(self.volume, "z"), [1, 2])
            self.assertEqual(region_nonempty_indices(self.volume, "y"), [2, 3, 4])
        self.assertFalse(scan.called)

    def test_a_measured_empty_roi_answers_without_reading_the_mask(self):
        self.volume.region_mask_coverage = 0.0
        self.volume.save(update_fields=["region_mask_coverage"])
        with patch(
            "volumes.region_masks.calculate_region_nonempty_indices",
            side_effect=AssertionError("the empty ROI was read"),
        ):
            self.assertEqual(region_nonempty_indices(self.volume, "z"), [])

    def test_unmeasured_coverage_is_not_treated_as_empty(self):
        self.assertIsNone(self.volume.region_mask_coverage)
        self.assertEqual(region_nonempty_indices(self.volume, "z"), [1, 2])

    def test_unknown_axis_and_missing_mask_are_refused(self):
        with self.assertRaisesMessage(ValueError, "Unknown axis"):
            region_nonempty_indices(self.volume, "t")
        self.volume.region_mask_path = ""
        with self.assertRaisesMessage(ValueError, "has no region mask"):
            region_nonempty_indices(self.volume, "z")


class WholeInstanceRegionDisplayTests(_RegionVolumeMixin, TestCase):
    def setUp(self):
        self.volume = self.build_volume(owner_name="region-display-labels")

    def tearDown(self):
        self.tear_down_volume()

    def test_an_instance_touching_the_region_survives_whole(self):
        labels = np.zeros((6, 6), dtype=np.int32)
        labels[1:3, 1:3] = 3
        region = np.zeros((6, 6), dtype=np.uint8)
        region[2:5, 1:4] = 1  # covers only the lower half of instance 3

        shown = labels_touching_region(labels, region)

        # Whole instance, including the rows outside the ROI — this is the
        # difference from the CSS mask this replaced.
        np.testing.assert_array_equal(shown, labels)

    def test_an_instance_that_never_enters_the_region_is_hidden(self):
        labels = np.zeros((6, 6), dtype=np.int32)
        labels[0, 4:6] = 4
        region = np.zeros((6, 6), dtype=np.uint8)
        region[2:5, 1:4] = 1

        np.testing.assert_array_equal(
            labels_touching_region(labels, region), np.zeros((6, 6), dtype=np.int32)
        )

    def test_an_empty_region_plane_hides_everything(self):
        labels = np.full((4, 4), 9, dtype=np.int32)
        np.testing.assert_array_equal(
            labels_touching_region(labels, np.zeros((4, 4), dtype=np.uint8)),
            np.zeros((4, 4), dtype=np.int32),
        )

    def test_region_filtered_png_differs_from_the_unfiltered_one(self):
        plain = render_label_slice_png("label.tif", "z", 1)
        filtered = render_label_slice_png(
            "label.tif", "z", 1, region_location="roi.tif"
        )
        self.assertNotEqual(plain, filtered)
        # A plane with no labels inside the ROI renders to something, not a 500.
        self.assertTrue(
            render_label_slice_png("label.tif", "z", 0, region_location="roi.tif")
        )

    def test_filtered_and_unfiltered_renders_do_not_share_a_cache_entry(self):
        first = render_label_slice_png("label.tif", "z", 1)
        filtered = render_label_slice_png(
            "label.tif", "z", 1, region_location="roi.tif"
        )
        self.assertEqual(render_label_slice_png("label.tif", "z", 1), first)
        self.assertEqual(
            render_label_slice_png("label.tif", "z", 1, region_location="roi.tif"),
            filtered,
        )


class RegionDisplayApiTests(_RegionVolumeMixin, APITestCase):
    def setUp(self):
        self.volume = self.build_volume(owner_name="region-display-api")
        self.manager = get_user_model().objects.create_superuser("region-mgr")
        self.client.force_authenticate(user=self.manager)

    def tearDown(self):
        self.tear_down_volume()

    def test_region_index_lists_the_planes_holding_region(self):
        response = self.client.get(
            reverse("api-volume-region-index", args=[self.volume.pk]), {"axis": "z"}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["axis"], "z")
        self.assertEqual(response.data["indices"], [1, 2])
        self.assertEqual(response.data["length"], 2)

    def test_region_index_404s_for_a_volume_without_a_region_mask(self):
        self.volume.region_mask_path = ""
        self.volume.save(update_fields=["region_mask_path"])
        response = self.client.get(
            reverse("api-volume-region-index", args=[self.volume.pk]), {"axis": "z"}
        )
        self.assertEqual(response.status_code, 404)

    def test_region_index_rejects_an_unknown_axis(self):
        response = self.client.get(
            reverse("api-volume-region-index", args=[self.volume.pk]), {"axis": "q"}
        )
        self.assertEqual(response.status_code, 400)

    def test_region_index_is_denied_to_a_user_who_cannot_view_the_volume(self):
        outsider = get_user_model().objects.create_user("region-outsider")
        self.client.force_authenticate(user=outsider)
        response = self.client.get(
            reverse("api-volume-region-index", args=[self.volume.pk]), {"axis": "z"}
        )
        self.assertEqual(response.status_code, 403)

    def test_label_slice_region_only_filters_and_writes_nothing(self):
        label_before = hashlib.sha256(self.label_path.read_bytes()).hexdigest()
        region_before = hashlib.sha256(self.region_path.read_bytes()).hexdigest()

        plain = self.client.get(
            reverse("api-volume-label-slice", args=[self.volume.pk]),
            {"axis": "z", "index": 1},
        )
        filtered = self.client.get(
            reverse("api-volume-label-slice", args=[self.volume.pk]),
            {"axis": "z", "index": 1, "region_only": "1"},
        )
        self.assertEqual(plain.status_code, 200)
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered["Content-Type"], "image/png")
        self.assertNotEqual(plain.content, filtered.content)

        # The one thing a display feature must never do.
        self.assertEqual(
            hashlib.sha256(self.label_path.read_bytes()).hexdigest(), label_before
        )
        self.assertEqual(
            hashlib.sha256(self.region_path.read_bytes()).hexdigest(), region_before
        )

    def test_label_slice_region_only_is_ignored_without_a_region_mask(self):
        self.volume.region_mask_path = ""
        self.volume.save(update_fields=["region_mask_path"])
        response = self.client.get(
            reverse("api-volume-label-slice", args=[self.volume.pk]),
            {"axis": "z", "index": 1, "region_only": "1"},
        )
        self.assertEqual(response.status_code, 200)
