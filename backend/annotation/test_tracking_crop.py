"""Crop planning for prompts spread across a large EM plane."""

import numpy as np
from django.test import SimpleTestCase, override_settings

from annotation.tracking import xy_crop


def seed(height, width, y, x, size=6):
    mask = np.zeros((height, width), dtype=bool)
    mask[y : y + size, x : x + size] = True
    return mask


@override_settings(MITO_SAM2_XY_PAD=256, MITO_SAM2_XY_MIN=512, MITO_SAM2_XY_MAX=2048)
class SeedClusteringTests(SimpleTestCase):
    # The production planes this runs on.
    H, W = 3885, 4544

    def test_prompts_further_apart_than_one_window_get_a_window_each(self):
        # Regression: a single square window capped at MITO_SAM2_XY_MAX was
        # centred on the combined bounding box. Two prompts ~3800px apart on a
        # 4544-wide plane both fell outside it, so `crop_seeds` reduced each to
        # an empty mask and both branches propagated as nothing -- the
        # annotator's prompts simply disappeared.
        seeds = {
            1: {0: seed(self.H, self.W, 100, 200)},
            2: {0: seed(self.H, self.W, 120, 4000)},
        }
        groups = xy_crop.cluster_seeds(seeds, self.H, self.W)
        self.assertEqual(len(groups), 2, "one window cannot hold both")
        self.assertEqual([sorted(g) for g in groups], [[1], [2]])

        # And each window actually contains its own seed.
        for group in groups:
            roi = xy_crop.plan_xy_roi(group, self.H, self.W)
            for per_z in xy_crop.crop_seeds(group, roi).values():
                for mask in per_z.values():
                    self.assertTrue(mask.any(), "seed must survive its own crop")

    def test_the_old_single_window_really_did_lose_them(self):
        # Guards the diagnosis itself: without grouping, both seeds crop away.
        seeds = {
            1: {0: seed(self.H, self.W, 100, 200)},
            2: {0: seed(self.H, self.W, 120, 4000)},
        }
        roi = xy_crop.plan_xy_roi(seeds, self.H, self.W)
        lost = [
            branch
            for branch, per_z in xy_crop.crop_seeds(seeds, roi).items()
            if not any(mask.any() for mask in per_z.values())
        ]
        self.assertEqual(sorted(lost), [1, 2])

    def test_nearby_prompts_still_share_one_window(self):
        seeds = {
            1: {0: seed(self.H, self.W, 100, 200)},
            2: {0: seed(self.H, self.W, 140, 260)},
        }
        groups = xy_crop.cluster_seeds(seeds, self.H, self.W)
        self.assertEqual(len(groups), 1)
        roi = xy_crop.plan_xy_roi(groups[0], self.H, self.W)
        for per_z in xy_crop.crop_seeds(groups[0], roi).values():
            for mask in per_z.values():
                self.assertTrue(mask.any())

    def test_each_window_is_only_as_big_as_its_own_prompts(self):
        # Grouping is also what keeps propagation quick: a distant second prompt
        # must not inflate the window the first one is tracked in.
        alone = {1: {0: seed(self.H, self.W, 100, 200)}}
        with_far = {**alone, 2: {0: seed(self.H, self.W, 120, 4000)}}
        near = xy_crop.plan_xy_roi(alone, self.H, self.W)
        grouped = xy_crop.cluster_seeds(with_far, self.H, self.W)
        first = xy_crop.plan_xy_roi(groups_for(grouped, 1), self.H, self.W)
        self.assertEqual(
            (first.y0, first.y1, first.x0, first.x1),
            (near.y0, near.y1, near.x0, near.x1),
        )

    def test_a_branch_with_no_pixels_still_round_trips(self):
        seeds = {
            1: {0: seed(self.H, self.W, 100, 200)},
            2: {0: np.zeros((self.H, self.W), dtype=bool)},
        }
        groups = xy_crop.cluster_seeds(seeds, self.H, self.W)
        self.assertEqual(sorted(b for g in groups for b in g), [1, 2])

    def test_grouping_is_deterministic(self):
        seeds = {
            3: {0: seed(self.H, self.W, 120, 4000)},
            1: {0: seed(self.H, self.W, 100, 200)},
            2: {0: seed(self.H, self.W, 140, 260)},
        }
        first = [sorted(g) for g in xy_crop.cluster_seeds(seeds, self.H, self.W)]
        second = [sorted(g) for g in xy_crop.cluster_seeds(dict(reversed(list(seeds.items()))), self.H, self.W)]
        self.assertEqual(first, second)


def groups_for(groups, branch):
    return next(g for g in groups if branch in g)
