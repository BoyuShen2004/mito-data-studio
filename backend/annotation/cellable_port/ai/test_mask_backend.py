"""Backend selection for the interactive mask tools."""

from __future__ import annotations

import threading
import unittest.mock

import numpy as np
from django.test import SimpleTestCase, override_settings

from annotation.cellable_port.ai import registry
from annotation.cellable_port.ai.sam2_feature_cache import DiskFeatureStore
from annotation.cellable_port.ai.sam2_masks import Sam2Masks

# Plausibility is measured against the plane, so these fixtures need a plane
# rather than the 8x8 scratch the call-shape tests can get away with.
PLANE = (200, 200)


def _image():
    return np.zeros(PLANE, dtype=np.uint8)


class FakeWrapper:
    """Stands in for the loaded SAM 2 image model."""

    def __init__(self, mask=None, candidates=None, ious=None):
        self.calls = []
        self.encoded = []
        self.store = None
        self._mask = mask
        # What a single point makes SAM 2 emit: several masks, each with the
        # model's own predicted IoU. `candidates` sets them explicitly.
        self._candidates = candidates
        self._ious = ious

    def set_feature_store(self, store):
        self.store = store

    def warm_slice(self, image, *, cache_key):
        self.encoded.append(cache_key)
        return "miss"

    def is_slice_warm(self, cache_key):
        return cache_key in self.encoded

    def predict_single_frame(
        self, image, *, points=None, point_labels=None, box=None, cache_key=None,
        candidates=False,
    ):
        self.calls.append(
            {"points": points, "point_labels": point_labels, "box": box, "cache_key": cache_key}
        )
        if self._candidates is not None:
            masks = [self._fit(m, image.shape[:2]) for m in self._candidates]
        elif self._mask is not None:
            masks = [self._fit(self._mask, image.shape[:2])]
        else:
            mask = np.zeros(image.shape[:2], dtype=bool)
            mask[1:3, 1:3] = True
            masks = [mask]
        if not candidates:
            return masks[0]
        ious = self._ious if self._ious is not None else [1.0] * len(masks)
        return masks, np.asarray(ious, dtype=float)

    @staticmethod
    def _fit(mask, shape):
        mask = np.asarray(mask, dtype=bool)
        if mask.shape == shape:
            return mask
        from PIL import Image

        return (
            np.asarray(
                Image.fromarray(mask.astype(np.uint8) * 255).resize(
                    (shape[1], shape[0]), Image.NEAREST
                )
            )
            > 0
        )


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
        mask = np.zeros(PLANE, dtype=bool)
        mask[20:60, 20:60] = True   # the object
        mask[90, 90] = True         # a one-pixel fleck, under the 5% threshold
        model = Sam2Masks(FakeWrapper(mask=mask), threading.RLock())

        out = model.predict_mask_from_points(_image(), [[40, 40]], [1])

        self.assertTrue(out[20:60, 20:60].all())
        self.assertFalse(out[90, 90])

    def test_pinholes_are_cleaned_on_the_same_terms_as_flecks(self):
        # An organelle mask pitted with single-pixel holes reads as texture in
        # the preview, because every one of them gets its own traced contour.
        mask = np.zeros(PLANE, dtype=bool)
        mask[20:60, 20:60] = True
        mask[30, 30] = False
        mask[45, 51] = False
        model = Sam2Masks(FakeWrapper(mask=mask), threading.RLock())

        out = model.predict_mask_from_points(_image(), [[40, 40]], [1])

        self.assertTrue(out[30, 30])
        self.assertTrue(out[45, 51])


class PointMaskChoiceTests(SimpleTestCase):
    """Which of SAM 2's answers a click actually gets.

    A single point makes the model emit three masks ranked by its own
    predicted IoU. On densely packed EM that ranking routinely puts a
    near-full-frame blanket on top, which is the "meaningless green" an
    annotator sees. The click is the extra information that settles it.
    """

    @staticmethod
    def _blob(y, x, half=20):
        mask = np.zeros(PLANE, dtype=bool)
        mask[y - half:y + half, x - half:x + half] = True
        return mask

    def test_only_the_component_holding_the_click_survives(self):
        # Two separated lobes of the same size: the size threshold cannot tell
        # them apart, and only one of them is the object that was clicked.
        mask = self._blob(40, 40) | self._blob(40, 160)
        model = Sam2Masks(FakeWrapper(mask=mask), threading.RLock())

        out = model.predict_mask_from_points(_image(), [[40, 40]], [1])

        self.assertTrue(out[40, 40])
        self.assertFalse(out[40, 160].any())

    def test_a_lobe_touching_no_click_is_kept_when_it_touches_another(self):
        # Two positive points on two lobes is a deliberate "both of these",
        # not a stray — refining a prompt must not throw half of it away.
        mask = self._blob(40, 40) | self._blob(40, 160)
        model = Sam2Masks(FakeWrapper(mask=mask), threading.RLock())

        out = model.predict_mask_from_points(_image(), [[40, 40], [160, 40]], [1, 1])

        self.assertTrue(out[40, 40])
        self.assertTrue(out[40, 160])

    def test_the_near_full_frame_candidate_loses_to_a_worse_ranked_lobe(self):
        blanket = np.ones(PLANE, dtype=bool)
        lobe = self._blob(40, 40)
        model = Sam2Masks(
            FakeWrapper(candidates=[blanket, lobe], ious=[0.97, 0.12]), threading.RLock()
        )

        out = model.predict_mask_from_points(_image(), [[40, 40]], [1])

        self.assertEqual(int(out.sum()), int(lobe.sum()))

    def test_a_hairline_candidate_loses_to_a_worse_ranked_lobe(self):
        ribbon = np.zeros(PLANE, dtype=bool)
        ribbon[40, 10:200] = True   # one pixel tall — a membrane, not an object
        lobe = self._blob(40, 40)
        model = Sam2Masks(
            FakeWrapper(candidates=[ribbon, lobe], ious=[0.95, 0.30]), threading.RLock()
        )

        out = model.predict_mask_from_points(_image(), [[40, 40]], [1])

        self.assertEqual(int(out.sum()), int(lobe.sum()))

    def test_the_best_ranked_plausible_candidate_still_wins(self):
        # The filter is a gate, not a re-ranking: among masks that could be one
        # organelle, SAM 2's own order is the one to trust.
        small = self._blob(40, 40, half=6)
        large = self._blob(40, 40, half=20)
        model = Sam2Masks(
            FakeWrapper(candidates=[small, large], ious=[0.40, 0.90]), threading.RLock()
        )

        out = model.predict_mask_from_points(_image(), [[40, 40]], [1])

        self.assertEqual(int(out.sum()), int(large.sum()))

    def test_relaxed_fallback_keeps_an_anchored_mid_size_mask(self):
        # 20% of the plane is above the strict organelle ceiling but matches
        # the useful podo candidates seen at dense/boundary clicks. It must
        # beat the higher-scored full-frame shred instead of returning empty.
        mid = np.zeros(PLANE, dtype=bool)
        mid[20:100, 20:120] = True
        model = Sam2Masks(
            FakeWrapper(candidates=[np.ones(PLANE, dtype=bool), mid], ious=[0.99, 0.12]),
            threading.RLock(),
        )

        out = model.predict_mask_from_points(_image(), [[40, 40]], [1])

        self.assertEqual(int(out.sum()), int(mid.sum()))

    def test_fallback_candidates_must_cover_every_positive_click(self):
        first_only = np.zeros(PLANE, dtype=bool)
        first_only[20:100, 20:120] = True
        both = np.zeros(PLANE, dtype=bool)
        both[20:100, 20:145] = True
        model = Sam2Masks(
            FakeWrapper(candidates=[first_only, both], ious=[0.95, 0.20]),
            threading.RLock(),
        )

        out = model.predict_mask_from_points(
            _image(), [[40, 40], [140, 40]], [1, 1]
        )

        self.assertTrue(out[40, 40])
        self.assertTrue(out[40, 140])
        self.assertEqual(int(out.sum()), int(both.sum()))

    @override_settings(MITO_AI_MASK_FALLBACK_MAX_PLANE_FRACTION=0.25)
    def test_last_resort_chooses_the_smallest_anchored_non_blanket(self):
        # Neither answer fits the relaxed 25% gate. The smaller 32% component
        # is still a refinable preview and stays below the hard 50% guard.
        smaller = np.zeros(PLANE, dtype=bool)
        smaller[20:120, 20:148] = True
        larger = np.zeros(PLANE, dtype=bool)
        larger[10:170, 10:110] = True
        model = Sam2Masks(
            FakeWrapper(candidates=[larger, smaller], ious=[0.90, 0.10]),
            threading.RLock(),
        )

        out = model.predict_mask_from_points(_image(), [[40, 40]], [1])

        self.assertEqual(int(out.sum()), int(smaller.sum()))

    def test_nothing_plausible_returns_nothing_rather_than_the_blanket(self):
        # The viewer turns an empty answer into "add another point / use Box
        # Mask", which is true; painting the blanket instead is not.
        model = Sam2Masks(
            FakeWrapper(candidates=[np.ones(PLANE, dtype=bool)], ious=[0.99]),
            threading.RLock(),
        )

        out = model.predict_mask_from_points(_image(), [[40, 40]], [1])

        self.assertEqual(out.shape, PLANE)
        self.assertFalse(out.any())

    def test_a_candidate_that_misses_the_click_is_refused(self):
        model = Sam2Masks(FakeWrapper(mask=self._blob(40, 160)), threading.RLock())

        out = model.predict_mask_from_points(_image(), [[40, 40]], [1])

        self.assertFalse(out.any())

    @override_settings(MITO_AI_MASK_FALLBACK_MAX_PLANE_FRACTION=0.99)
    def test_fallback_override_cannot_enable_a_near_full_plane_shred(self):
        shred = np.ones(PLANE, dtype=bool)
        shred[:5, :5] = False
        model = Sam2Masks(FakeWrapper(mask=shred), threading.RLock())

        out = model.predict_mask_from_points(_image(), [[40, 40]], [1])

        self.assertFalse(out.any())

    def test_negative_points_do_not_anchor_anything(self):
        # A negative click marks what to exclude, so a component holding only
        # negative points is not the object being asked for.
        mask = self._blob(40, 40) | self._blob(40, 160)
        model = Sam2Masks(FakeWrapper(mask=mask), threading.RLock())

        out = model.predict_mask_from_points(_image(), [[40, 40], [160, 40]], [1, 0])

        self.assertTrue(out[40, 40])
        self.assertFalse(out[40, 160].any())

    @override_settings(MITO_AI_MASK_MAX_PLANE_FRACTION=0.9)
    def test_the_ceiling_is_tunable_for_volumes_with_huge_objects(self):
        blanket = np.zeros(PLANE, dtype=bool)
        blanket[10:190, 10:190] = True
        model = Sam2Masks(FakeWrapper(mask=blanket), threading.RLock())

        out = model.predict_mask_from_points(_image(), [[40, 40]], [1])

        self.assertEqual(int(out.sum()), int(blanket.sum()))


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
