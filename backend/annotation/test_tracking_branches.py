"""Automatic child inference + touch/merge lifecycle for SAM2 Track.

Everything here exercises the **provider-independent** layer: connected-
component decomposition, cross-layer component association, contact detection,
branch termination and the lineage audit trail. No GPU, no model, no Django
models — the "provider" is a scripted stand-in that returns exactly the masks a
test asks for, so each rule is pinned on its own rather than on whatever SAM2
happens to predict.

Layer numbering here is API z (0-based) throughout; the 1-based layer numbers
are a frontend presentation concern (``frontend/src/features/viewer/annotate/
trackRange.ts``).
"""

from __future__ import annotations

import numpy as np
from django.test import TestCase, override_settings

from annotation.tracking import config
from annotation.tracking.components import infer_branches, split_components
from annotation.tracking.contact import (
    contact_strength,
    resolve_branch_contacts,
)
from annotation.tracking.interfaces import (
    PropagationResult,
    TrackingProvider,
)
from annotation.tracking.services import (
    assert_seeds_within_range,
    run_branch_tracking,
    validate_z_range,
)

PLANE = (40, 40)


def blob(y0, y1, x0, x1, shape=PLANE):
    """A filled rectangle mask; bounds are half-open, like array slicing."""
    mask = np.zeros(shape, dtype=bool)
    mask[y0:y1, x0:x1] = True
    return mask


def bright(z_size, shape=PLANE):
    return np.full((z_size, *shape), 200, dtype=np.uint8)


class ScriptedProvider(TrackingProvider):
    """Returns caller-declared masks, so tests own the propagated geometry.

    ``script(seed_slices, z)`` is called once per branch per layer and returns
    that branch's mask (or ``None`` for "absent here"). It receives the
    branch's own seed slices, which is how a test tells its branches apart —
    the temporary provider object ids are allocated by the service and are
    deliberately not something a test should have to predict.
    """

    name = "scripted"
    requires_gpu = False

    def __init__(self, script):
        self.script = script
        self.seen_seeds: list[dict] = []

    def propagate(self, request):
        self.seen_seeds.append(
            {int(bid): dict(per_z) for bid, per_z in request.seeds.items()}
        )
        lo, hi = request.z_range
        result = PropagationResult()
        for bid, seed_slices in request.seeds.items():
            per_z = {}
            for z in range(lo, hi + 1):
                mask = self.script(seed_slices, z)
                if mask is not None and np.any(mask):
                    per_z[z] = np.asarray(mask, dtype=bool)
            result.masks[int(bid)] = per_z
        return result


def carry_seed(seed_slices, z):
    """Rigidly carry a branch's nearest seed to every layer in the range."""
    nearest = min(seed_slices, key=lambda seed_z: (abs(seed_z - z), seed_z))
    return seed_slices[nearest]


def constant(masks_by_z):
    """A script that ignores the seeds and replays a fixed ``{z: mask}``."""
    return lambda _seeds, z: masks_by_z.get(z)


# --------------------------------------------------------------------------
# 1-6: seed decomposition and cross-layer association
# --------------------------------------------------------------------------


class ComponentInferenceTests(TestCase):
    def test_one_connected_prompt_stays_one_branch(self):
        """The everyday case is unchanged: one blob, one branch, one label."""
        inference = infer_branches({1: {2: blob(4, 10, 4, 10)}})
        self.assertEqual(len(inference.branches), 1)
        self.assertEqual(inference.branches[0].branch_key, 1)
        self.assertEqual(inference.branches[0].seed_zs, [2])
        self.assertEqual(inference.warnings, [])

    def test_two_disconnected_components_become_two_branches(self):
        inference = infer_branches({1: {0: blob(2, 8, 2, 8) | blob(2, 8, 20, 26)}})
        self.assertEqual(len(inference.branches), 2)
        self.assertEqual([b.branch_key for b in inference.branches], [1, 2])
        # Both came from the one manual child the user never had to create.
        self.assertEqual({b.subclass_index for b in inference.branches}, {1})

    def test_two_disconnected_components_become_two_provider_objects(self):
        """End to end: two blobs are propagated as two independent SAM2 objects."""
        provider = ScriptedProvider(carry_seed)
        volume = np.zeros((5, *PLANE), dtype=np.int32)
        seed = blob(2, 8, 2, 8) | blob(2, 8, 20, 26)
        result = run_branch_tracking(
            image=bright(5),
            volume_mask=volume,
            seeds={},
            branch_seeds={1: {0: seed}},
            z_range=(0, 4),
            provider=provider,
            group_id=17,
        )
        self.assertEqual(len(provider.seen_seeds[0]), 2)
        self.assertEqual(len(result["group"]["inferred_branches"]), 2)
        self.assertEqual(len(set(result["branch_ids"])), 2)
        # ...and the volume still holds exactly one instance id.
        self.assertEqual(set(np.unique(volume)) - {0}, {17})

    def test_three_components_get_deterministic_branch_keys(self):
        seed = blob(2, 8, 2, 8) | blob(2, 8, 16, 22) | blob(20, 26, 2, 8)
        first = infer_branches({1: {0: seed}})
        second = infer_branches({1: {0: seed}})
        self.assertEqual(len(first.branches), 3)
        self.assertEqual([b.branch_key for b in first.branches], [1, 2, 3])
        self.assertEqual(
            [b.audit() for b in first.branches], [b.audit() for b in second.branches]
        )
        # Raster order of the first pixel, so the keys are reproducible.
        self.assertEqual(
            [b.components[0].centroid[0] < 10 for b in first.branches],
            [True, True, False],
        )

    def test_second_prompt_layer_associates_without_identity_swap(self):
        """Scan order is not identity.

        ``a`` moves down the plane between the two prompted layers while ``b``
        stays put, so on the second layer ``b``'s component is the one that
        comes first in raster order. Pairing by component index would hand
        ``a``'s branch ``b``'s geometry; matching on predicted location must
        not.
        """
        a_first, a_second = blob(2, 8, 2, 8), blob(12, 18, 3, 9)
        b_first, b_second = blob(2, 8, 20, 26), blob(2, 8, 21, 27)
        inference = infer_branches(
            {1: {0: a_first | b_first, 4: a_second | b_second}}
        )
        self.assertEqual(len(inference.branches), 2)
        by_key = {b.branch_key: b for b in inference.branches}
        # Branch 1 was seeded from ``a`` and must still be following ``a``.
        np.testing.assert_array_equal(by_key[1].seeds[0], a_first)
        np.testing.assert_array_equal(by_key[1].seeds[4], a_second)
        np.testing.assert_array_equal(by_key[2].seeds[0], b_first)
        np.testing.assert_array_equal(by_key[2].seeds[4], b_second)
        self.assertEqual(by_key[1].seed_zs, [0, 4])
        self.assertEqual(by_key[2].seed_zs, [0, 4])

    def test_unmatched_later_component_starts_a_new_branch(self):
        """A structure that only appears later is a new branch, not a jump."""
        inference = infer_branches(
            {1: {0: blob(2, 8, 2, 8), 4: blob(2, 8, 3, 9) | blob(30, 36, 30, 36)}}
        )
        self.assertEqual(len(inference.branches), 2)
        by_key = {b.branch_key: b for b in inference.branches}
        self.assertEqual(by_key[1].seed_zs, [0, 4])
        self.assertEqual(by_key[2].seed_zs, [4])
        self.assertGreater(by_key[2].components[0].centroid[0], 25)

    def test_no_component_is_assigned_to_two_branches(self):
        """Two branches converging on one later blob: one continues, one stops."""
        inference = infer_branches(
            {1: {0: blob(2, 8, 8, 14) | blob(2, 8, 16, 22), 1: blob(2, 8, 12, 18)}}
        )
        assigned = [
            b.branch_key for b in inference.branches if 1 in b.seeds
        ]
        self.assertEqual(len(assigned), 1)
        self.assertEqual(len(inference.branches), 2)

    def test_tiny_specks_are_filtered_when_a_real_component_survives(self):
        seed = blob(4, 12, 4, 12)
        seed[30, 30] = True  # a one-pixel crumb from a clipped brush stroke
        inference = infer_branches({1: {0: seed}})
        self.assertEqual(len(inference.branches), 1)
        self.assertEqual(len(inference.dropped), 1)
        self.assertEqual(inference.dropped[0]["area"], 1)
        self.assertEqual(inference.dropped[0]["reason"], "below_min_component_area")

    def test_a_deliberately_tiny_prompt_is_never_silently_dropped(self):
        """The filter is relative: it never empties a layer.

        A small mitochondrion the annotator drew on purpose has to survive, or
        the propagation would either lose it or fail outright for "no seeds".
        """
        inference = infer_branches({1: {0: blob(5, 6, 5, 6)}})
        self.assertEqual(len(inference.branches), 1)
        self.assertEqual(inference.branches[0].components[0].area, 1)
        self.assertEqual(inference.dropped, [])

    @override_settings(MITO_TRACK_MIN_COMPONENT_AREA=50)
    def test_speck_threshold_is_configurable(self):
        seed = blob(4, 14, 4, 14)  # 100 px, survives
        seed |= blob(30, 34, 30, 34)  # 16 px, now below the raised floor
        inference = infer_branches({1: {0: seed}})
        self.assertEqual(len(inference.branches), 1)
        self.assertEqual(inference.dropped[0]["min_component_area"], 50)

    def test_manual_children_are_never_associated_with_each_other(self):
        """Manual children stay separate identities — the advanced override."""
        inference = infer_branches(
            {1: {0: blob(2, 8, 2, 8)}, 2: {0: blob(2, 8, 3, 9)}}
        )
        self.assertEqual(len(inference.branches), 2)
        self.assertEqual(
            {b.branch_key: b.subclass_index for b in inference.branches}, {1: 1, 2: 2}
        )

    def test_a_manual_child_holding_two_blobs_is_still_split(self):
        inference = infer_branches(
            {1: {0: blob(2, 8, 2, 8) | blob(2, 8, 20, 26)}, 2: {0: blob(30, 36, 2, 8)}}
        )
        self.assertEqual(len(inference.branches), 3)
        self.assertEqual(
            [b.subclass_index for b in inference.branches], [1, 1, 2]
        )

    def test_split_components_is_raster_ordered_and_speck_aware(self):
        mask = blob(2, 8, 20, 26) | blob(2, 8, 2, 8)
        parts = split_components(mask)
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0][2:8, 2:8].all())  # leftmost first


# --------------------------------------------------------------------------
# 7-11: the explicit, inclusive Start/End range
# --------------------------------------------------------------------------


class ExplicitRangeTests(TestCase):
    def test_explicit_range_is_preserved_not_recomputed_from_seeds(self):
        provider = ScriptedProvider(carry_seed)
        volume = np.zeros((10, *PLANE), dtype=np.int32)
        result = run_branch_tracking(
            image=bright(10),
            volume_mask=volume,
            seeds={},
            branch_seeds={1: {5: blob(4, 10, 4, 10)}},
            z_range=(1, 8),
            provider=provider,
            group_id=17,
        )
        self.assertEqual(result["group"]["start_z"], 1)
        self.assertEqual(result["group"]["end_z"], 8)
        # The seed sat on layer 5 alone; the range is emphatically not [5, 5].
        self.assertEqual(result["group"]["seed_zs"], [5])

    def test_one_seed_propagates_across_the_whole_wider_range(self):
        provider = ScriptedProvider(carry_seed)
        volume = np.zeros((10, *PLANE), dtype=np.int32)
        run_branch_tracking(
            image=bright(10),
            volume_mask=volume,
            seeds={},
            branch_seeds={1: {5: blob(4, 10, 4, 10)}},
            z_range=(1, 8),
            provider=provider,
            group_id=17,
        )
        painted = sorted(z for z in range(10) if np.any(volume[z] == 17))
        self.assertEqual(painted, [1, 2, 3, 4, 5, 6, 7, 8])

    def test_missing_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Start and End layers are required"):
            validate_z_range(None, 10)

    def test_reversed_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not be before Start layer"):
            validate_z_range((8, 3), 10)

    def test_out_of_bounds_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "past the last layer"):
            validate_z_range((0, 10), 10)
        with self.assertRaisesRegex(ValueError, "below the first layer"):
            validate_z_range((-1, 4), 10)

    def test_range_is_inclusive_at_both_ends(self):
        self.assertEqual(validate_z_range((0, 9), 10), (0, 9))
        self.assertEqual(validate_z_range((4, 4), 10), (4, 4))

    def test_a_seed_outside_the_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, r"Seed layer\(s\) 9 fall outside"):
            assert_seeds_within_range({1: {9: blob(4, 10, 4, 10)}}, 1, 8)

    def test_an_empty_seed_plane_outside_the_range_is_not_an_error(self):
        assert_seeds_within_range(
            {1: {5: blob(4, 10, 4, 10), 9: np.zeros(PLANE, dtype=bool)}}, 1, 8
        )

    def test_run_branch_tracking_rejects_a_seed_outside_its_range(self):
        volume = np.zeros((10, *PLANE), dtype=np.int32)
        with self.assertRaisesRegex(ValueError, "fall outside"):
            run_branch_tracking(
                image=bright(10),
                volume_mask=volume,
                seeds={},
                branch_seeds={1: {9: blob(4, 10, 4, 10)}},
                z_range=(1, 8),
                provider=ScriptedProvider(carry_seed),
                group_id=17,
            )
        self.assertFalse(volume.any())


# --------------------------------------------------------------------------
# 12-18: the child touch/merge lifecycle
# --------------------------------------------------------------------------


class ContactMetricTests(TestCase):
    def test_overlap_counts_as_contact(self):
        self.assertGreater(contact_strength(blob(0, 4, 0, 4), blob(2, 6, 2, 6)), 0)

    def test_direct_8_adjacency_counts_as_touch(self):
        # Column 4 and column 5, four rows tall: touching, not overlapping.
        self.assertEqual(contact_strength(blob(0, 4, 4, 5), blob(0, 4, 5, 6)), 4)

    def test_diagonal_adjacency_counts_as_touch(self):
        self.assertEqual(contact_strength(blob(0, 1, 0, 1), blob(1, 2, 1, 2)), 1)

    def test_a_two_pixel_gap_is_not_contact(self):
        self.assertEqual(contact_strength(blob(0, 4, 4, 5), blob(0, 4, 6, 7)), 0)

    def test_empty_masks_never_contact(self):
        self.assertEqual(
            contact_strength(np.zeros(PLANE, dtype=bool), blob(0, 4, 0, 4)), 0
        )


class BranchLifecycleTests(TestCase):
    """``resolve_branch_contacts`` in isolation, on hand-built mask stacks."""

    def _apart(self, z_size=6):
        """Two branches that never come near each other."""
        return {
            1: {z: blob(2, 12, 2, 12) for z in range(z_size)},
            2: {z: blob(2, 12, 26, 36) for z in range(z_size)},
        }

    def _touching_from(self, z_first, z_size=6, columns=1):
        """Big branch 1 and small branch 2, in contact from ``z_first`` on."""
        masks = {1: {}, 2: {}}
        for z in range(z_size):
            masks[1][z] = blob(2, 12, 2, 12)
            masks[2][z] = (
                blob(4, 4 + columns, 12, 16) if z >= z_first else blob(4, 8, 20, 24)
            )
        return masks

    def test_separate_children_both_run_to_end(self):
        masks = self._apart()
        resolution = resolve_branch_contacts(masks, start_z=0, end_z=5)
        self.assertEqual(resolution.events, [])
        self.assertEqual(resolution.terminated_at, {})
        self.assertEqual(sorted(masks[1]), list(range(6)))
        self.assertEqual(sorted(masks[2]), list(range(6)))

    def test_sustained_contact_terminates_the_smaller_child(self):
        masks = self._touching_from(3, columns=4)
        resolution = resolve_branch_contacts(masks, start_z=0, end_z=5)
        self.assertEqual(len(resolution.events), 1)
        event = resolution.events[0]
        self.assertEqual(event.survivor_branch, 1)
        self.assertEqual(event.loser_branch, 2)
        self.assertEqual(event.reason, "smaller_branch")
        # Backdated to the first layer of the confirmed run, not to where the
        # evidence finally crossed the threshold.
        self.assertEqual(event.contact_z, 3)
        self.assertEqual(resolution.terminated_at, {2: 3})

    def test_both_children_contribute_on_the_contact_layer(self):
        masks = self._touching_from(3, columns=4)
        resolve_branch_contacts(masks, start_z=0, end_z=5)
        self.assertIn(3, masks[2])
        self.assertTrue(np.any(masks[2][3]))
        self.assertTrue(np.any(masks[1][3]))
        # Only *later* layers are discarded.
        self.assertEqual(sorted(masks[2]), [0, 1, 2, 3])
        self.assertEqual(sorted(masks[1]), [0, 1, 2, 3, 4, 5])

    def test_a_single_layer_noisy_touch_does_not_terminate(self):
        masks = self._apart()
        masks[2][3] = blob(4, 5, 12, 13)  # one pixel, 8-adjacent, one layer only
        resolution = resolve_branch_contacts(masks, start_z=0, end_z=5)
        self.assertEqual(resolution.events, [])
        self.assertEqual(sorted(masks[2]), list(range(6)))

    def test_a_one_pixel_touch_sustained_for_many_layers_does_not_terminate(self):
        masks = self._apart()
        for z in (2, 3, 4):
            masks[2][z] = blob(4, 5, 12, 13)  # still only one contact pixel
        resolution = resolve_branch_contacts(masks, start_z=0, end_z=5)
        self.assertEqual(resolution.events, [])

    def test_a_strong_single_layer_contact_is_enough(self):
        masks = self._apart()
        masks[2][3] = blob(2, 12, 12, 22)  # a full-height overlap-adjacent slab
        resolution = resolve_branch_contacts(masks, start_z=0, end_z=5)
        self.assertEqual(len(resolution.events), 1)
        self.assertEqual(resolution.events[0].contact_z, 3)

    def test_contact_broken_and_remade_restarts_the_run(self):
        """Two isolated weak touches are not one sustained contact."""
        masks = self._apart()
        masks[2][1] = blob(4, 6, 12, 14)
        masks[2][4] = blob(4, 6, 12, 14)
        resolution = resolve_branch_contacts(masks, start_z=0, end_z=5)
        self.assertEqual(resolution.events, [])

    def test_a_child_with_a_later_prompt_wins_even_when_smaller(self):
        masks = self._touching_from(3, columns=4)
        resolution = resolve_branch_contacts(
            masks, start_z=0, end_z=5, seed_zs={1: [0], 2: [0, 5]}
        )
        self.assertEqual(len(resolution.events), 1)
        self.assertEqual(resolution.events[0].survivor_branch, 2)
        self.assertEqual(resolution.events[0].loser_branch, 1)
        self.assertEqual(resolution.events[0].reason, "later_prompt")
        self.assertEqual(resolution.terminated_at, {1: 3})

    def test_two_later_prompts_are_ambiguous_and_kill_neither(self):
        masks = self._touching_from(3, columns=4)
        resolution = resolve_branch_contacts(
            masks, start_z=0, end_z=5, seed_zs={1: [0, 5], 2: [0, 5]}
        )
        self.assertEqual(resolution.events, [])
        self.assertEqual(resolution.terminated_at, {})
        self.assertEqual(len(resolution.warnings), 1)
        self.assertEqual(resolution.warnings[0]["code"], "ambiguous_child_merge")
        self.assertEqual(resolution.warnings[0]["branches"], [1, 2])
        # Both survive to End for review.
        self.assertEqual(sorted(masks[1]), list(range(6)))
        self.assertEqual(sorted(masks[2]), list(range(6)))

    def test_an_ambiguous_pair_warns_once_not_once_per_layer(self):
        masks = self._touching_from(2, columns=4)
        resolution = resolve_branch_contacts(
            masks, start_z=0, end_z=5, seed_zs={1: [0, 5], 2: [0, 5]}
        )
        self.assertEqual(len(resolution.warnings), 1)

    def test_equal_children_fall_back_to_the_stable_branch_id(self):
        masks = {
            1: {z: blob(2, 12, 2, 12) for z in range(6)},
            2: {z: blob(2, 12, 12, 22) for z in range(6)},
        }
        resolution = resolve_branch_contacts(masks, start_z=0, end_z=5)
        self.assertEqual(len(resolution.events), 1)
        self.assertEqual(resolution.events[0].survivor_branch, 1)
        self.assertEqual(resolution.events[0].reason, "stable_branch_id")

    def test_three_way_contact_is_deterministic_and_acyclic(self):
        """Three children pile up on one layer; the lineage must be a forest."""

        def build():
            masks = {1: {}, 2: {}, 3: {}}
            for z in range(6):
                masks[1][z] = blob(2, 22, 2, 12)  # largest
                if z >= 3:
                    masks[2][z] = blob(2, 12, 12, 18)  # medium, touching 1
                    masks[3][z] = blob(12, 22, 12, 16)  # smallest, touching 1
                else:
                    masks[2][z] = blob(2, 12, 30, 36)
                    masks[3][z] = blob(12, 22, 30, 34)
            return masks

        first = resolve_branch_contacts(build(), start_z=0, end_z=5)
        second = resolve_branch_contacts(build(), start_z=0, end_z=5)
        self.assertEqual(
            [e.to_dict() for e in first.events], [e.to_dict() for e in second.events]
        )
        self.assertTrue(first.events)
        # Every loser dies exactly once, and no branch both loses and survives
        # a later event — that is what makes the lineage acyclic.
        losers = [e.loser_branch for e in first.events]
        self.assertEqual(len(losers), len(set(losers)))
        for event in first.events:
            self.assertNotIn(event.survivor_branch, losers[: losers.index(event.loser_branch)])
        self.assertEqual(set(first.terminated_at), set(losers))
        # The biggest branch is the one still running at End.
        self.assertEqual({1, 2, 3} - set(losers), {1})

    def test_a_terminated_child_cannot_take_part_in_later_contacts(self):
        masks = {
            1: {z: blob(2, 22, 2, 12) for z in range(6)},
            2: {z: blob(2, 12, 12, 18) for z in range(6)},
            3: {z: blob(2, 12, 18, 30) for z in range(6)},
        }
        resolution = resolve_branch_contacts(masks, start_z=0, end_z=5)
        losers = [e.loser_branch for e in resolution.events]
        self.assertEqual(len(losers), len(set(losers)))
        for loser in losers:
            self.assertEqual(max(masks[loser]), resolution.terminated_at[loser])

    def test_canonical_order_is_start_to_end_not_seed_order(self):
        """Contact is backdated to the low-z end of the run, always."""
        masks = self._touching_from(3, columns=4)
        resolution = resolve_branch_contacts(masks, start_z=0, end_z=5)
        self.assertEqual(resolution.events[0].contact_z, 3)
        # Same masks, a range that starts later: the run now starts at start_z.
        masks = self._touching_from(3, columns=4)
        resolution = resolve_branch_contacts(masks, start_z=4, end_z=5)
        self.assertEqual(resolution.events[0].contact_z, 4)

    @override_settings(MITO_TRACK_STRONG_CONTACT_PIXELS=2)
    def test_contact_thresholds_are_configurable(self):
        masks = self._apart()
        masks[2][3] = blob(4, 6, 12, 14)  # two contact pixels, one layer
        resolution = resolve_branch_contacts(masks, start_z=0, end_z=5)
        self.assertEqual(len(resolution.events), 1)

    def test_config_defaults_are_readable_and_sane(self):
        self.assertGreaterEqual(config.strong_contact_pixels(), config.min_contact_pixels())
        self.assertGreaterEqual(config.contact_sustain_layers(), 2)
        self.assertGreaterEqual(config.min_component_area(), 1)


# --------------------------------------------------------------------------
# 19-21: end-to-end orchestration guarantees
# --------------------------------------------------------------------------


class OrchestrationTests(TestCase):
    def _run(self, *, script, branch_seeds, z_size=6, group_id=17, **kwargs):
        volume = kwargs.pop("volume", None)
        if volume is None:
            volume = np.zeros((z_size, *PLANE), dtype=np.int32)
        provider = ScriptedProvider(script)
        result = run_branch_tracking(
            image=bright(z_size),
            volume_mask=volume,
            seeds={},
            branch_seeds=branch_seeds,
            z_range=(0, z_size - 1),
            provider=provider,
            group_id=group_id,
            **kwargs,
        )
        return volume, result, provider

    def test_two_children_both_propagate_through_end(self):
        volume, result, _ = self._run(
            script=carry_seed,
            branch_seeds={1: {0: blob(2, 12, 2, 12) | blob(2, 12, 26, 36)}},
        )
        self.assertEqual(len(result["group"]["inferred_branches"]), 2)
        self.assertEqual(result["group"]["merge_events"], [])
        for z in range(6):
            self.assertTrue(volume[z][2:12, 2:12].all())
            self.assertTrue(volume[z][2:12, 26:36].all())

    def test_merged_children_leave_a_continuous_parent_mask(self):
        def script(seed_slices, z):
            left = np.any(seed_slices[0][:, :20])
            if left:
                return blob(2, 12, 2, 12)
            return blob(4, 8, 12, 16) if z >= 3 else blob(4, 8, 26, 30)

        volume, result, _ = self._run(
            script=script,
            branch_seeds={1: {0: blob(2, 12, 2, 12) | blob(4, 8, 26, 30)}},
        )
        events = result["group"]["merge_events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["contact_z"], 3)
        # The survivor is continuous through End...
        for z in range(6):
            self.assertTrue(volume[z][2:12, 2:12].all())
        # ...the loser contributes up to and including the merge layer...
        self.assertTrue(volume[3][4:8, 12:16].all())
        # ...and nothing of it survives past it.
        self.assertFalse(volume[4][4:8, 12:16].any())
        self.assertFalse(volume[5][4:8, 12:16].any())

    def test_the_final_label_never_contains_a_temporary_branch_id(self):
        volume, result, _ = self._run(
            script=carry_seed,
            branch_seeds={
                1: {0: blob(2, 12, 2, 12) | blob(2, 12, 26, 36) | blob(26, 36, 2, 12)}
            },
        )
        self.assertEqual(len(result["branch_ids"]), 3)
        self.assertEqual(set(np.unique(volume)) - {0}, {17})
        for branch_id in result["branch_ids"]:
            if branch_id != 17:
                self.assertFalse(np.any(volume == branch_id))

    def test_audit_explains_which_components_became_which_branch(self):
        _volume, result, _ = self._run(
            script=carry_seed,
            branch_seeds={1: {0: blob(2, 12, 2, 12), 3: blob(2, 12, 3, 13)}},
        )
        branches = result["group"]["inferred_branches"]
        self.assertEqual(len(branches), 1)
        self.assertEqual(branches[0]["seed_zs"], [0, 3])
        self.assertEqual(
            [c["z"] for c in branches[0]["components"]], [0, 3]
        )
        self.assertEqual(branches[0]["subclass_index"], 1)
        self.assertIn(str(branches[0]["branch_key"]), result["group"]["branch_provider_ids"])

    def test_rerunning_the_same_prompts_reproduces_the_audit(self):
        seeds = {1: {0: blob(2, 12, 2, 12) | blob(2, 12, 26, 36)}}
        _v1, first, _ = self._run(script=carry_seed, branch_seeds=seeds)
        _v2, second, _ = self._run(script=carry_seed, branch_seeds=seeds)
        self.assertEqual(first["group"], second["group"])

    def test_empty_voxels_only_still_protects_an_unrelated_label(self):
        volume = np.zeros((6, *PLANE), dtype=np.int32)
        volume[:, 5, 5] = 99
        volume[:, 5, 30] = 99
        self._run(
            script=carry_seed,
            branch_seeds={1: {0: blob(2, 12, 2, 12) | blob(2, 12, 26, 36)}},
            volume=volume,
        )
        self.assertTrue(np.all(volume[:, 5, 5] == 99))
        self.assertTrue(np.all(volume[:, 5, 30] == 99))

    def test_all_voxels_may_replace_an_unrelated_label(self):
        volume = np.zeros((6, *PLANE), dtype=np.int32)
        volume[:, 5, 5] = 99
        self._run(
            script=carry_seed,
            branch_seeds={1: {0: blob(2, 12, 2, 12) | blob(2, 12, 26, 36)}},
            volume=volume,
            protect_other_labels=False,
        )
        self.assertTrue(np.all(volume[:, 5, 5] == 17))

    def test_a_child_touching_an_unrelated_label_is_not_a_child_merge(self):
        """Contact with a *different* label is an overwrite question, not lineage."""
        volume = np.zeros((6, *PLANE), dtype=np.int32)
        volume[:, 2:12, 13:20] = 99  # sits right beside the only child
        _volume, result, _ = self._run(
            script=carry_seed,
            branch_seeds={1: {0: blob(2, 12, 2, 12)}},
            volume=volume,
        )
        self.assertEqual(result["group"]["merge_events"], [])
        self.assertEqual(result["group"]["terminated_at"], {})
        self.assertTrue(np.all(volume[:, 2:12, 13:20] == 99))

    def test_provider_failure_leaves_the_volume_untouched(self):
        class FailingProvider(TrackingProvider):
            def propagate(self, request):
                raise RuntimeError("provider failed")

        volume = np.zeros((6, *PLANE), dtype=np.int32)
        volume[0, 0, 0] = 9
        before = volume.copy()
        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            run_branch_tracking(
                image=bright(6),
                volume_mask=volume,
                seeds={},
                branch_seeds={1: {0: blob(2, 12, 2, 12)}},
                z_range=(0, 5),
                provider=FailingProvider(),
                group_id=17,
            )
        np.testing.assert_array_equal(volume, before)

    def test_a_provider_echoing_an_unknown_object_id_is_ignored(self):
        class NoisyProvider(TrackingProvider):
            def propagate(self, request):
                result = PropagationResult()
                for bid, per_z in request.seeds.items():
                    result.masks[int(bid)] = dict(per_z)
                result.masks[9999] = {0: blob(30, 36, 30, 36)}
                return result

        volume = np.zeros((6, *PLANE), dtype=np.int32)
        run_branch_tracking(
            image=bright(6),
            volume_mask=volume,
            seeds={},
            branch_seeds={1: {0: blob(2, 12, 2, 12)}},
            z_range=(0, 5),
            provider=NoisyProvider(),
            group_id=17,
        )
        self.assertFalse(volume[0][30:36, 30:36].any())

    def test_masks_outside_the_explicit_range_are_discarded(self):
        class OvereagerProvider(TrackingProvider):
            def propagate(self, request):
                result = PropagationResult()
                for bid in request.seeds:
                    result.masks[int(bid)] = {
                        z: blob(2, 12, 2, 12) for z in range(6)
                    }
                return result

        volume = np.zeros((6, *PLANE), dtype=np.int32)
        run_branch_tracking(
            image=bright(6),
            volume_mask=volume,
            seeds={},
            branch_seeds={1: {2: blob(2, 12, 2, 12)}},
            z_range=(1, 3),
            provider=OvereagerProvider(),
            group_id=17,
        )
        self.assertEqual(sorted(z for z in range(6) if volume[z].any()), [1, 2, 3])


# --------------------------------------------------------------------------
# Adapter-specific behaviour (no GPU required)
# --------------------------------------------------------------------------


class LocalAdapterTests(TestCase):
    """The dev/CI stand-in has to honour the explicit range in both directions."""

    def _propagate(self, seed_z, z_range, z_size=8):
        from annotation.tracking.adapters.local import LocalTrackingProvider
        from annotation.tracking.interfaces import PropagationRequest

        request = PropagationRequest(
            image=bright(z_size),
            seeds={1: {seed_z: blob(4, 12, 4, 12)}},
            z_range=z_range,
        )
        return LocalTrackingProvider().propagate(request)

    def test_a_seed_propagates_backward_to_start_z(self):
        """Regression: the backward carry used to be a silent no-op.

        Its ``start``/``stop`` arguments were transposed, so its loop condition
        was false on the first iteration. Nothing noticed while the range was
        derived from the seed bounds — ``start_z`` *was* the earliest seed —
        and it became visible the moment Start could sit below the first seed.
        """
        result = self._propagate(5, (1, 6))
        self.assertEqual(sorted(result.masks[1]), [1, 2, 3, 4, 5, 6])

    def test_propagation_stays_inside_the_explicit_range(self):
        result = self._propagate(4, (3, 5))
        self.assertEqual(sorted(result.masks[1]), [3, 4, 5])

    def test_a_single_layer_range_propagates_only_that_layer(self):
        result = self._propagate(4, (4, 4))
        self.assertEqual(sorted(result.masks[1]), [4])


class Sam2AdapterDirectionTests(TestCase):
    """SAM2 keeps inferring bidirectionally; only *ordering* moved out of it.

    Forward from the earliest seed, backward from the latest, unioned — that is
    a prediction-quality decision and is deliberately unchanged. Merge/collision
    ordering is the canonical ``start_z -> end_z`` and lives in
    :mod:`annotation.tracking.contact` instead. Asserted against a fake SAM
    session so it runs without CUDA or the checkpoint.
    """

    class FakeSam:
        def __init__(self):
            self.calls = []
            self.prompts = []

        def reset_session(self):
            self.calls.append("reset")

        def initialize_sequence(self, stack):
            self.calls.append(("init", stack.shape))

        def add_mask_prompt(self, local_z, obj_id, mask):
            self.prompts.append((local_z, obj_id))

        def propagate_multi(self, start, z_range, direction, backward_start_slice):
            self.calls.append(
                ("propagate", start, z_range, direction, backward_start_slice)
            )
            return {}

    def test_inference_is_bidirectional_from_the_outermost_seeds(self):
        from annotation.tracking.adapters.sam2 import Sam2TrackingProvider

        sam = self.FakeSam()
        provider = Sam2TrackingProvider()
        seeds = {7: {2: blob(4, 10, 4, 10), 6: blob(4, 10, 5, 11)}}
        provider._propagate_crop(sam, bright(8), seeds, z_lo=0)
        call = next(c for c in sam.calls if c[0] == "propagate")
        _name, start, z_range, direction, backward_start = call
        self.assertEqual(direction, "both")
        self.assertEqual(start, 2)  # forward from the earliest seed
        self.assertEqual(backward_start, 6)  # backward from the latest
        self.assertEqual(z_range, (0, 7))

    def test_every_branch_is_registered_as_its_own_object_id(self):
        from annotation.tracking.adapters.sam2 import Sam2TrackingProvider

        sam = self.FakeSam()
        provider = Sam2TrackingProvider()
        seeds = {7: {2: blob(4, 10, 4, 10)}, 8: {2: blob(4, 10, 20, 26)}}
        provider._propagate_crop(sam, bright(8), seeds, z_lo=0)
        self.assertEqual(sorted(obj for _z, obj in sam.prompts), [7, 8])
