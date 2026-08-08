"""Phase 11 pure cores — the mag ladder and the block reduction.

Neither module imports Django or zarr, so these run as plain unit tests against
hand-computed expectations. That is deliberate: the maths is the part worth
pinning, and it is only pinnable if verifying it does not need a database row
and a volume on disk.
"""

from __future__ import annotations

import numpy as np
from django.test import SimpleTestCase

from volumes.pyramid.downsample import (
    REDUCTION_MEAN,
    REDUCTION_MODE,
    default_reduction,
    reduce_block,
    reduce_slab,
    slab_plan,
)
from volumes.pyramid.ladder import (
    MagLevel,
    build_ladder,
    chunk_shape_for,
    ladder_summary,
    relative_factors,
)


class LadderTests(SimpleTestCase):
    def test_isotropic_voxels_double_every_axis(self):
        levels = build_ladder((512, 512, 512), (1.0, 1.0, 1.0))
        self.assertEqual(levels[0].factors, (1, 1, 1))
        self.assertEqual(levels[1].factors, (2, 2, 2))
        self.assertEqual(levels[2].factors, (4, 4, 4))
        self.assertEqual(levels[1].shape, (256, 256, 256))

    def test_anisotropic_voxels_hold_z_until_xy_catches_up(self):
        """40 nm z, 8 nm xy — z must not be downsampled five times too fast."""
        levels = build_ladder((256, 1024, 1024), (40.0, 8.0, 8.0))
        factors = [lv.factors for lv in levels]
        self.assertEqual(factors[0], (1, 1, 1))
        self.assertEqual(factors[1], (1, 2, 2))
        self.assertEqual(factors[2], (1, 4, 4))
        # By now xy extent is 32 nm against z's 40 — within tolerance, so all
        # three double together from here.
        self.assertEqual(factors[3], (2, 8, 8))

    def test_array_name_is_the_xy_factor(self):
        """Doc 20 asks for mags 1,2,4,8 — anisotropy lives in the attributes."""
        levels = build_ladder((256, 1024, 1024), (40.0, 8.0, 8.0))
        self.assertEqual([lv.name for lv in levels[:4]], ["1", "2", "4", "8"])

    def test_missing_voxel_size_is_treated_as_isotropic(self):
        for voxel in (None, (0.0, 0.0, 0.0), (1.0, 0.0, 1.0)):
            levels = build_ladder((256, 256, 256), voxel)
            self.assertEqual(levels[1].factors, (2, 2, 2))

    def test_ladder_stops_before_a_level_becomes_too_small(self):
        levels = build_ladder((64, 64, 64), (1.0, 1.0, 1.0), min_extent=32)
        # 64 -> 32 is allowed; 32 -> 16 is not.
        self.assertEqual([lv.shape for lv in levels], [(64, 64, 64), (32, 32, 32)])

    def test_a_thin_z_axis_does_not_veto_further_xy_levels(self):
        """A 4-plane volume must still gain xy mags."""
        levels = build_ladder((4, 1024, 1024), (40.0, 8.0, 8.0), min_extent=32)
        self.assertGreater(len(levels), 2)
        self.assertTrue(all(lv.shape[0] == 4 for lv in levels[:3]))

    def test_shapes_use_ceiling_division_so_nothing_is_truncated(self):
        levels = build_ladder((9, 9, 9), (1.0, 1.0, 1.0), min_extent=1)
        self.assertEqual(levels[1].shape, (5, 5, 5))  # not 4

    def test_single_voxel_volume_yields_only_full_resolution(self):
        self.assertEqual(len(build_ladder((1, 1, 1))), 1)

    def test_invalid_shapes_are_refused(self):
        with self.assertRaises(ValueError):
            build_ladder((0, 10, 10))
        with self.assertRaises(ValueError):
            build_ladder((10, 10))  # type: ignore[arg-type]

    def test_relative_factors_step_from_the_level_above(self):
        levels = build_ladder((256, 1024, 1024), (40.0, 8.0, 8.0))
        self.assertEqual(relative_factors(levels[1], levels[0]), (1, 2, 2))
        self.assertEqual(relative_factors(levels[3], levels[2]), (2, 2, 2))

    def test_relative_factors_reject_a_non_multiple(self):
        parent = MagLevel(level=0, factors=(1, 3, 1), shape=(8, 8, 8))
        child = MagLevel(level=1, factors=(1, 4, 1), shape=(8, 2, 8))
        with self.assertRaises(ValueError):
            relative_factors(child, parent)

    def test_chunks_are_slice_oriented_and_clipped(self):
        self.assertEqual(chunk_shape_for((100, 2048, 2048)), (1, 512, 512))
        self.assertEqual(chunk_shape_for((100, 64, 30)), (1, 64, 30))

    def test_summary_is_serialisable(self):
        summary = ladder_summary(build_ladder((256, 256, 256)))
        self.assertEqual(summary["count"], len(summary["levels"]))
        self.assertEqual(summary["levels"][0]["factors"], [1, 1, 1])


class ReductionTests(SimpleTestCase):
    def test_mean_of_a_uniform_block_is_that_value(self):
        block = np.full((2, 4, 4), 7, dtype=np.uint16)
        out = reduce_block(block, (2, 2, 2), reduction=REDUCTION_MEAN)
        self.assertEqual(out.shape, (1, 2, 2))
        self.assertTrue((out == 7).all())

    def test_mean_is_computed_without_integer_overflow(self):
        """Averaging uint16 in-place would wrap on a bright block."""
        block = np.full((2, 2, 2), 65535, dtype=np.uint16)
        out = reduce_block(block, (2, 2, 2), reduction=REDUCTION_MEAN)
        self.assertEqual(int(out[0, 0, 0]), 65535)

    def test_mean_preserves_dtype(self):
        block = np.arange(8, dtype=np.uint8).reshape(2, 2, 2)
        self.assertEqual(
            reduce_block(block, (2, 2, 2), reduction=REDUCTION_MEAN).dtype, np.uint8
        )

    def test_mode_picks_the_most_common_label_not_the_average(self):
        """The mean of ids 3 and 7 is 5 — a different object entirely."""
        block = np.array([[[3, 3], [3, 7]]], dtype=np.uint16)
        out = reduce_block(block, (1, 2, 2), reduction=REDUCTION_MODE)
        self.assertEqual(int(out[0, 0, 0]), 3)

    def test_mode_never_invents_a_label_that_was_not_present(self):
        rng = np.random.default_rng(11)
        block = rng.choice([0, 5, 9, 21], size=(4, 8, 8)).astype(np.uint16)
        out = reduce_block(block, (2, 2, 2), reduction=REDUCTION_MODE)
        self.assertTrue(set(np.unique(out)).issubset({0, 5, 9, 21}))

    def test_mode_keeps_a_thin_structure_rather_than_losing_it_to_background(self):
        """Background wins only when the block is entirely background."""
        block = np.zeros((1, 2, 2), dtype=np.uint16)
        block[0, 0, 0] = 4  # one voxel of a thin process in a 4-voxel block
        out = reduce_block(block, (1, 2, 2), reduction=REDUCTION_MODE)
        self.assertEqual(int(out[0, 0, 0]), 4)

    def test_mode_returns_background_for_an_empty_block(self):
        block = np.zeros((2, 2, 2), dtype=np.uint16)
        out = reduce_block(block, (2, 2, 2), reduction=REDUCTION_MODE)
        self.assertEqual(int(out[0, 0, 0]), 0)

    def test_partial_trailing_block_still_produces_an_output_voxel(self):
        block = np.arange(3 * 3 * 3, dtype=np.uint16).reshape(3, 3, 3)
        out = reduce_block(block, (2, 2, 2), reduction=REDUCTION_MEAN)
        self.assertEqual(out.shape, (2, 2, 2))

    def test_padding_uses_edge_values_so_labels_are_not_eroded(self):
        """Zero-padding would dilute a boundary block toward background."""
        block = np.full((1, 1, 3), 6, dtype=np.uint16)
        out = reduce_block(block, (1, 1, 2), reduction=REDUCTION_MODE)
        self.assertEqual(out.shape, (1, 1, 2))
        self.assertTrue((out == 6).all())

    def test_identity_factors_copy_rather_than_alias(self):
        block = np.ones((2, 2, 2), dtype=np.uint16)
        out = reduce_block(block, (1, 1, 1))
        out[0, 0, 0] = 9
        self.assertEqual(int(block[0, 0, 0]), 1)

    def test_unknown_reduction_and_bad_factors_are_refused(self):
        block = np.ones((2, 2, 2), dtype=np.uint16)
        with self.assertRaises(ValueError):
            reduce_block(block, (2, 2, 2), reduction="median")
        with self.assertRaises(ValueError):
            reduce_block(block, (0, 2, 2))
        with self.assertRaises(ValueError):
            reduce_block(np.ones((2, 2)), (2, 2, 2))

    def test_default_reduction_follows_what_the_data_means(self):
        self.assertEqual(default_reduction("uint16", is_label=True), REDUCTION_MODE)
        self.assertEqual(default_reduction("uint16", is_label=False), REDUCTION_MEAN)


class SlabTests(SimpleTestCase):
    def test_slab_plan_covers_every_parent_plane_exactly_once(self):
        plan = slab_plan(10, 3)
        self.assertEqual(plan, [(0, 3), (3, 6), (6, 9), (9, 10)])
        covered = sum(b - a for a, b in plan)
        self.assertEqual(covered, 10)

    def test_slab_plan_with_factor_one_is_plane_by_plane(self):
        self.assertEqual(slab_plan(3, 1), [(0, 1), (1, 2), (2, 3)])

    def test_a_slab_reduces_to_exactly_one_output_plane(self):
        slab = np.arange(2 * 4 * 4, dtype=np.uint16).reshape(2, 4, 4)
        out = reduce_slab(slab, (2, 2, 2))
        self.assertEqual(out.shape, (1, 2, 2))

    def test_a_short_trailing_slab_still_yields_one_plane(self):
        slab = np.ones((1, 4, 4), dtype=np.uint16)
        out = reduce_slab(slab, (2, 2, 2))
        self.assertEqual(out.shape, (1, 2, 2))

    def test_a_mismatched_slab_is_refused_rather_than_writing_the_wrong_z(self):
        slab = np.ones((4, 4, 4), dtype=np.uint16)
        with self.assertRaises(ValueError):
            reduce_slab(slab, (2, 2, 2))  # 4 planes / factor 2 -> 2 output planes

    def test_slab_reduction_matches_whole_block_reduction(self):
        """Bounded and dense must agree — that is what makes streaming safe."""
        rng = np.random.default_rng(7)
        volume = rng.integers(0, 500, size=(8, 16, 16), dtype=np.uint16)
        factors = (2, 2, 2)

        dense = reduce_block(volume, factors, reduction=REDUCTION_MEAN)
        planes = [
            reduce_slab(volume[z0:z1], factors, reduction=REDUCTION_MEAN)
            for z0, z1 in slab_plan(volume.shape[0], factors[0])
        ]
        streamed = np.concatenate(planes, axis=0)
        np.testing.assert_array_equal(streamed, dense)

    def test_slab_reduction_matches_dense_for_labels_too(self):
        rng = np.random.default_rng(3)
        volume = rng.choice([0, 2, 5], size=(6, 8, 8)).astype(np.uint16)
        factors = (3, 2, 2)
        dense = reduce_block(volume, factors, reduction=REDUCTION_MODE)
        streamed = np.concatenate(
            [
                reduce_slab(volume[z0:z1], factors, reduction=REDUCTION_MODE)
                for z0, z1 in slab_plan(volume.shape[0], factors[0])
            ],
            axis=0,
        )
        np.testing.assert_array_equal(streamed, dense)
