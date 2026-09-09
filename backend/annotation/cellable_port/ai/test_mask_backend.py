"""Backend selection for the interactive mask tools."""

from __future__ import annotations

import threading
import unittest.mock

import numpy as np
from django.test import SimpleTestCase

from annotation.cellable_port.ai import registry
from annotation.cellable_port.ai.sam2_feature_cache import DiskFeatureStore
from annotation.cellable_port.ai.sam2_masks import Sam2Masks


class FakeWrapper:
    """Stands in for the loaded SAM 2 image model."""

    def __init__(self, mask=None):
        self.calls = []
        self.encoded = []
        self.store = None
        self._mask = mask

    def set_feature_store(self, store):
        self.store = store

    def warm_slice(self, image, *, cache_key):
        self.encoded.append(cache_key)
        return "miss"

    def is_slice_warm(self, cache_key):
        return cache_key in self.encoded

    def predict_single_frame(self, image, *, points=None, point_labels=None, box=None, cache_key=None):
        self.calls.append(
            {"points": points, "point_labels": point_labels, "box": box, "cache_key": cache_key}
        )
        if self._mask is not None:
            return self._mask
        mask = np.zeros(image.shape[:2], dtype=bool)
        mask[1:3, 1:3] = True
        return mask


class Sam2MasksAdapterTests(SimpleTestCase):
    def setUp(self):
        registry.reset_mask_model()
        self.addCleanup(registry.reset_mask_model)

    def test_point_prompts_pass_through_with_their_labels(self):
        wrapper = FakeWrapper()
        model = Sam2Masks(wrapper, threading.RLock())

        model.predict_mask_from_points(
            np.zeros((8, 8), dtype=np.uint8), [[2, 3], [5, 6]], [1, 0]
        )

        self.assertEqual(wrapper.calls[0]["points"], [(2.0, 3.0), (5.0, 6.0)])
        self.assertEqual(wrapper.calls[0]["point_labels"], [1, 0])

    def test_box_corners_are_normalised_to_min_max(self):
        wrapper = FakeWrapper()
        model = Sam2Masks(wrapper, threading.RLock())

        # A box dragged bottom-right to top-left arrives with reversed corners.
        model.predict_mask_from_box(np.zeros((8, 8), dtype=np.uint8), [[6, 7], [1, 2]])

        self.assertEqual(wrapper.calls[0]["box"], (1, 2, 6, 7))

    def test_the_slice_cache_key_is_the_embedding_path(self):
        wrapper = FakeWrapper()
        model = Sam2Masks(wrapper, threading.RLock())
        image = np.zeros((8, 8), dtype=np.uint8)

        model.predict_mask_from_points(image, [[2, 3]], [1], disk_path="/cache/z12.npy")
        model.predict_mask_from_points(image, [[4, 4]], [1], disk_path=None)

        self.assertEqual(wrapper.calls[0]["cache_key"], "/cache/z12.npy")
        # No path means "don't reuse anything" — never a key that could collide.
        self.assertIsNone(wrapper.calls[1]["cache_key"])

    def test_warming_encodes_the_slice_without_prompting_it(self):
        # Warm runs on slice-open, off the click path: it should pay the
        # encoder and nothing else — no prompt, no mask to discard.
        wrapper = FakeWrapper()
        model = Sam2Masks(wrapper, threading.RLock())

        model.warm(np.zeros((8, 8), dtype=np.uint8), disk_path="/cache/z12.npy")

        self.assertEqual(wrapper.encoded, ["/cache/z12.npy"])
        self.assertEqual(wrapper.calls, [])

    def test_warming_a_slice_with_no_identity_is_a_no_op(self):
        wrapper = FakeWrapper()
        model = Sam2Masks(wrapper, threading.RLock())

        model.warm(np.zeros((8, 8), dtype=np.uint8), disk_path=None)

        self.assertEqual(wrapper.encoded, [])
        self.assertEqual(wrapper.calls, [])

    def test_speckle_is_cleaned_the_way_efficientsam_cleans_it(self):
        mask = np.zeros((16, 16), dtype=bool)
        mask[2:10, 2:10] = True   # the object
        mask[14, 14] = True       # a one-pixel fleck, under the 5% threshold
        model = Sam2Masks(FakeWrapper(mask=mask), threading.RLock())

        out = model.predict_mask_from_points(np.zeros((16, 16), dtype=np.uint8), [[4, 4]], [1])

        self.assertTrue(out[2:10, 2:10].all())
        self.assertFalse(out[14, 14])


class MaskBackendSelectionTests(SimpleTestCase):
    def setUp(self):
        registry.reset_mask_model()
        self.addCleanup(registry.reset_mask_model)

    def test_the_tools_run_on_sam2_with_a_disk_cache_attached(self):
        provider = unittest.mock.Mock()
        wrapper = FakeWrapper()
        provider._load.return_value = wrapper
        with unittest.mock.patch(
            "annotation.tracking.registry.get_tracking_provider", return_value=provider
        ):
            model = registry.get_mask_model()

        self.assertIsInstance(model, Sam2Masks)
        # Without the L2 attached, every worker re-encodes what its siblings
        # already did — the whole point of wiring it here.
        self.assertIsInstance(wrapper.store, DiskFeatureStore)

    def test_a_missing_runtime_is_reported_not_silently_downgraded(self):
        # There is no fallback model any more: a GPU-less host must say the
        # tools are unavailable rather than quietly serve worse masks.
        with unittest.mock.patch(
            "annotation.tracking.registry.get_tracking_provider",
            side_effect=RuntimeError("no CUDA"),
        ):
            with self.assertRaises(registry.AiUnavailable) as caught:
                registry.get_mask_model()

        self.assertIn("no CUDA", str(caught.exception))

    def test_the_failure_is_remembered_rather_than_retried_per_request(self):
        with unittest.mock.patch(
            "annotation.tracking.registry.get_tracking_provider",
            side_effect=RuntimeError("no CUDA"),
        ) as loader:
            with self.assertRaises(registry.AiUnavailable):
                registry.get_mask_model()
            with self.assertRaises(registry.AiUnavailable):
                registry.get_mask_model()

        loader.assert_called_once()

    def test_the_backend_is_resolved_once_per_process(self):
        provider = unittest.mock.Mock()
        provider._load.return_value = FakeWrapper()
        with unittest.mock.patch(
            "annotation.tracking.registry.get_tracking_provider", return_value=provider
        ) as get_provider:
            first = registry.get_mask_model()
            second = registry.get_mask_model()
        self.assertIs(first, second)
        get_provider.assert_called_once()
