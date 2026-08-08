import numpy as np
from django.test import SimpleTestCase, override_settings

from annotation.cellable_port.ai.prompt_roi import RoiWindow
from annotation.tracking import xy_crop
from annotation.tracking.adapters.sam2 import Sam2TrackingProvider
from annotation.tracking.interfaces import PropagationRequest


class Sam2XyCropTests(SimpleTestCase):
    @override_settings(
        MITO_SAM2_XY_PAD=16, MITO_SAM2_XY_MAX=256, MITO_SAM2_XY_MIN=64
    )
    def test_crop_and_paste_preserve_seed(self):
        stack = np.zeros((4, 1000, 1000), dtype=np.uint8)
        seed = np.zeros((1000, 1000), dtype=bool)
        seed[400:420, 500:530] = True
        roi = xy_crop.plan_xy_roi({7: {1: seed}}, 1000, 1000)
        self.assertFalse(roi.covers(1000, 1000))
        self.assertLessEqual(max(roi.shape), 256)
        cropped = xy_crop.crop_seeds({7: {1: seed}}, roi)
        pasted = xy_crop.paste_masks(cropped, roi, seed.shape)
        np.testing.assert_array_equal(pasted[7][1], seed)
        self.assertEqual(xy_crop.crop_stack(stack, roi).shape[1:], roi.shape)

    def test_border_mask_expands_roi(self):
        roi = RoiWindow(0, 64, 0, 64)
        mask = np.zeros(roi.shape, dtype=bool)
        mask[0, 10:20] = True
        expanded = xy_crop.maybe_expand_for_border(
            roi, 400, 400, {1: {0: mask}}
        )
        self.assertIsNotNone(expanded)
        self.assertGreater(expanded.height, roi.height)


class _FakeSam:
    def __init__(self):
        self.reset_calls = 0
        self.init_calls = 0
        self.prompts = []
        self.stack = None

    def reset_session(self):
        self.reset_calls += 1
        self.prompts.clear()

    def initialize_sequence(self, stack):
        self.init_calls += 1
        self.stack = np.asarray(stack)

    def add_mask_prompt(self, local_z, obj_id, mask):
        self.prompts.append((int(obj_id), int(local_z)))

    def propagate_multi(self, start_slice, z_range, **kwargs):
        result = {}
        height, width = self.stack.shape[1:]
        for object_id, _ in self.prompts:
            result[object_id] = {}
            for z in range(z_range[0], z_range[1] + 1):
                mask = np.zeros((height, width), dtype=bool)
                mask[z % height, (object_id + z) % width] = True
                result[object_id][z] = mask
        return result


class Sam2ProviderContractTests(SimpleTestCase):
    def test_multi_object_uses_one_sequence_and_preserves_ids(self):
        provider = Sam2TrackingProvider()
        fake = _FakeSam()
        provider._sam = fake
        image = np.zeros((6, 32, 32), dtype=np.uint8)
        a = np.zeros((32, 32), dtype=bool)
        b = np.zeros((32, 32), dtype=bool)
        a[4:8, 4:8] = True
        b[10:14, 10:14] = True
        result = provider.propagate(
            PropagationRequest(
                image=image,
                seeds={11: {2: a}, 17: {3: b}},
                z_range=(1, 4),
            )
        )
        self.assertEqual(fake.reset_calls, 1)
        self.assertEqual(fake.init_calls, 1)
        self.assertEqual(sorted(fake.prompts), [(11, 1), (17, 2)])
        self.assertEqual(set(result.masks), {11, 17})
        self.assertIn(2, result.masks[11])
