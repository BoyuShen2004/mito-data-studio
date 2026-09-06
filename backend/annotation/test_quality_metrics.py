"""Overlap metric maths — pure functions, no database.

Deliberately fast and dependency-free so the arithmetic that decides an
annotator's quality score is checked on every run, not only when the slower
integration suites are exercised.
"""

from __future__ import annotations

import numpy as np
from django.test import SimpleTestCase

from annotation.quality_metrics import (
    MATCH_IOU,
    compare,
    contingency,
    instance_scores,
    semantic_scores,
    variation_of_information,
)


def score(candidate, reference):
    return compare([(np.array(candidate), np.array(reference))])


class SemanticOverlapTests(SimpleTestCase):
    def test_identical_volumes_score_perfectly(self):
        volume = [[0, 1, 1], [0, 1, 0], [2, 2, 0]]
        result = score(volume, volume)
        self.assertEqual(result["dice"], 1.0)
        self.assertEqual(result["iou"], 1.0)
        self.assertEqual(result["precision"], 1.0)
        self.assertEqual(result["recall"], 1.0)

    def test_two_empty_volumes_are_unmeasured_not_zero(self):
        """The distinction the whole module is built on.

        Nothing to score must not read as "completely wrong", or an empty
        gold-standard volume would tank an annotator's rolling score.
        """
        result = score([[0, 0]], [[0, 0]])
        for metric in ("dice", "iou", "precision", "recall", "instance_f1"):
            self.assertIsNone(result[metric], metric)

    def test_precision_and_recall_point_the_stated_way(self):
        # Candidate paints half of what the reference says is there.
        result = score([[1, 0, 0, 0]], [[1, 1, 0, 0]])
        self.assertEqual(result["precision"], 1.0)  # painted nothing spurious
        self.assertEqual(result["recall"], 0.5)  # missed half
        # And the reverse direction swaps them, proving the argument order is
        # not accidentally symmetric.
        flipped = score([[1, 1, 0, 0]], [[1, 0, 0, 0]])
        self.assertEqual(flipped["precision"], 0.5)
        self.assertEqual(flipped["recall"], 1.0)


class InstanceLevelTests(SimpleTestCase):
    def test_a_clean_split_is_perfect_dice_but_zero_instance_f1(self):
        """The reason instance metrics exist at all.

        One reference object cut in two scores 1.0 Dice — every foreground
        voxel agrees — while being unusable as instance segmentation.
        """
        result = score([[1, 1, 2, 2]], [[1, 1, 1, 1]])
        self.assertEqual(result["dice"], 1.0)
        self.assertEqual(result["instance_f1"], 0.0)
        self.assertEqual(result["false_splits"], 1)
        self.assertEqual(result["false_merges"], 0)

    def test_a_clean_merge_is_counted_as_a_merge(self):
        result = score([[1, 1, 1, 1]], [[1, 1, 2, 2]])
        self.assertEqual(result["dice"], 1.0)
        self.assertEqual(result["false_merges"], 1)
        self.assertEqual(result["false_splits"], 0)

    def test_matching_is_strictly_above_the_threshold(self):
        """At exactly 0.5 the assignment is not one-to-one.

        Counting a match at exactly the threshold let a perfectly halved
        object match twice and produced an F1 above 1.0.
        """
        # Each candidate half has IoU exactly 0.5 with the reference object.
        result = score([[1, 1, 2, 2]], [[1, 1, 1, 1]])
        self.assertEqual(result["matched"], 0)
        self.assertLessEqual(result["instance_f1"], 1.0)

        # Just above the threshold does match: 3 shared of 4 union = 0.75.
        good = score([[1, 1, 1, 0]], [[1, 1, 1, 1]])
        self.assertEqual(good["matched"], 1)
        self.assertEqual(good["instance_f1"], 1.0)

    def test_instance_f1_never_exceeds_one(self):
        for candidate, reference in (
            ([[1, 1, 2, 2]], [[1, 1, 1, 1]]),
            ([[1, 1, 1, 1]], [[1, 1, 2, 2]]),
            ([[1, 2, 3, 4]], [[1, 1, 1, 1]]),
            ([[1, 1, 1, 1]], [[1, 2, 3, 4]]),
        ):
            result = score(candidate, reference)
            self.assertLessEqual(result["instance_f1"], 1.0)
            self.assertGreaterEqual(result["instance_f1"], 0.0)

    def test_empty_and_populated_share_one_key_set(self):
        """A consumer must never have to branch on which keys came back."""
        self.assertEqual(
            set(score([[0, 0]], [[0, 0]])),
            set(score([[1, 1]], [[1, 1]])),
        )


class VariationOfInformationTests(SimpleTestCase):
    def test_identical_segmentations_have_zero_vi(self):
        volume = [[1, 1, 2, 2]]
        self.assertEqual(score(volume, volume)["variation_of_information"], 0.0)

    def test_a_split_raises_vi(self):
        result = score([[1, 1, 2, 2]], [[1, 1, 1, 1]])
        self.assertGreater(result["variation_of_information"], 0.0)

    def test_all_background_is_unmeasured(self):
        self.assertIsNone(score([[0, 0]], [[0, 0]])["variation_of_information"])


class ContingencyTests(SimpleTestCase):
    def test_large_instance_ids_do_not_alias(self):
        """The pack shift is derived from the data, not a fixed constant.

        A fixed 16-bit shift silently folded ids past 65535 onto each other,
        which would have merged two unrelated objects into one bucket.
        """
        candidate = np.array([[70000, 70000]])
        reference = np.array([[131072, 4]])
        counts = contingency([(candidate, reference)])
        self.assertEqual(counts[(70000, 131072)], 1)
        self.assertEqual(counts[(70000, 4)], 1)

    def test_slabs_accumulate_across_calls(self):
        """Reading in z-slabs must give the same answer as reading whole."""
        volume = np.arange(24).reshape(6, 2, 2) % 3
        whole = contingency([(volume, volume)])
        slabbed = contingency(
            [(volume[0:2], volume[0:2]), (volume[2:4], volume[2:4]),
             (volume[4:6], volume[4:6])]
        )
        self.assertEqual(whole, slabbed)

    def test_mismatched_slab_sizes_are_refused(self):
        with self.assertRaises(ValueError):
            contingency([(np.zeros((2, 2)), np.zeros((3, 3)))])
