"""HTTP plan/apply coverage for the Annotate flood-fill tool."""

import numpy as np
from django.test import override_settings

from annotation.models import AnnotationOperation
from annotation.test_interpolation_api import InterpolationApiTestCase

FULL_ON = override_settings(FEATURE_ANNOTATION_TOOLS=True, FEATURE_ANNOTATION_OPS=True)


@FULL_ON
class FloodFillApiTests(InterpolationApiTestCase):
    def _post_flood(self, **body):
        payload = {
            "axis": "z", "index": 2, "row": 4, "col": 4,
            "label": 7, "depth": 1, "overwrite_mode": "overwrite_empty",
            "mode": "preview",
        }
        payload.update(body)
        return self.client.post(
            f"/api/tasks/{self.task.pk}/flood-fill/", payload,
            content_type="application/json",
        )

    def test_preview_is_non_mutating_and_apply_records_one_operation(self):
        labels = np.zeros(self._endpoints().shape, dtype=np.int32)
        labels[2, :, 12] = 9  # a wall: fill must remain on the left
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(labels)
            before = self._read_working()
            preview = self._post_flood()
            np.testing.assert_array_equal(self._read_working(), before)
            applied = self._post_flood(mode="apply", idempotency_key="fill-1")
            after = self._read_working()
        self.assertEqual(preview.status_code, 200, preview.content[:300])
        self.assertEqual(applied.status_code, 200, applied.content[:300])
        self.assertTrue((after[2, :, :12] == 7).all())
        self.assertTrue((after[2, :, 13:] == 0).all())
        self.assertTrue((after[2, :, 12] == 9).all())
        self.assertEqual(AnnotationOperation.objects.count(), 1)
        self.assertEqual(AnnotationOperation.objects.get().payload["tool"], "flood_fill")

    def test_bounded_3d_fill_writes_multiple_z_slices(self):
        labels = np.zeros(self._endpoints().shape, dtype=np.int32)
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(labels)
            response = self._post_flood(mode="apply", depth=3)
            after = self._read_working()
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["slices_written"], [1, 2, 3])
        self.assertTrue((after[1:4] == 7).all())

    def test_x_y_views_support_2d_fill(self):
        labels = np.zeros(self._endpoints().shape, dtype=np.int32)
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(labels)
            response = self._post_flood(
                mode="apply", axis="y", index=3, row=2, col=4, depth=1
            )
            after = self._read_working()
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertTrue((after[:, 3, :] == 7).all())
