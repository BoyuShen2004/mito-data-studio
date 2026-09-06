"""Overlap metrics between two instance-label volumes.

Everything here is derived from **one contingency table** — a sparse map of
``(label in A, label in B) -> shared voxel count``. That is deliberate: Dice,
IoU, precision, recall, instance matching, false merges, false splits and
variation of information are all functions of that single table, so the
volumes are read exactly once, in z-slabs, and never held whole in memory.

Why instance-level metrics at all
---------------------------------
A mitochondria segmentation can score 0.95 Dice and still be worthless if
every neighbouring pair of objects is fused into one id. Semantic overlap
cannot see that; ``false_merges`` / ``false_splits`` can, which is why the EM
connectomics literature reports them alongside Dice rather than instead of it.

Conventions
-----------
* ``A`` is the **candidate** (what an annotator submitted), ``B`` is the
  **reference** (the trusted answer). Precision and recall are stated from
  that direction: precision penalises painting what is not there, recall
  penalises missing what is.
* Label ``0`` is background everywhere in this application.
* A metric that cannot be computed is returned as ``None``, never ``0.0``.
  An empty reference and a perfectly wrong answer must not look alike.
"""

from __future__ import annotations

import math
from collections import defaultdict

# A predicted and a reference object are considered to be "the same object,
# possibly split or merged" once they share at least this fraction of the
# smaller one. Only used for the split/merge counts, never for matching —
# matching uses IoU, which is symmetric and threshold-free at 0.5.
SIGNIFICANT_OVERLAP = 0.1

# Two instances are a match when their IoU is **strictly greater** than this.
# The strictness is load-bearing, not a style choice: above 0.5 the assignment
# is provably one-to-one, so no greedy tie-breaking is needed. At *exactly*
# 0.5 it is not — a reference object cut perfectly in half yields two
# candidates at IoU 0.5 each, and counting both matches produces an F1 above
# 1.0. Requiring `>` makes that case score 0 matches and one false split,
# which is the correct reading of a clean split.
MATCH_IOU = 0.5


def contingency(slab_pairs) -> dict[tuple[int, int], int]:
    """Accumulate ``(a_label, b_label) -> voxels`` across matching slabs.

    ``slab_pairs`` yields ``(a_slab, b_slab)`` numpy arrays of identical
    shape. Counting is vectorised per slab: the two label arrays are packed
    into one 64-bit key and handed to ``np.unique``, so the per-slab cost is
    one sort rather than a Python loop over voxels.
    """
    import numpy as np

    counts: dict[tuple[int, int], int] = defaultdict(int)
    for a_slab, b_slab in slab_pairs:
        a = np.asarray(a_slab).reshape(-1).astype(np.int64, copy=False)
        b = np.asarray(b_slab).reshape(-1).astype(np.int64, copy=False)
        if a.size != b.size:
            raise ValueError("Slab shapes differ between the two volumes")
        if a.size == 0:
            continue
        # Pack into one key. The shift must exceed the largest label present,
        # not a fixed constant: instance ids in a big volume routinely pass
        # 2**16, and a fixed shift would silently alias two distinct objects
        # into one bucket.
        span = int(max(b.max(initial=0), 0)) + 1
        keys, key_counts = np.unique(a * span + b, return_counts=True)
        for key, count in zip(keys.tolist(), key_counts.tolist()):
            counts[(key // span, key % span)] += count
    return dict(counts)


def _marginals(counts):
    a_sizes: dict[int, int] = defaultdict(int)
    b_sizes: dict[int, int] = defaultdict(int)
    total = 0
    for (a, b), n in counts.items():
        a_sizes[a] += n
        b_sizes[b] += n
        total += n
    return a_sizes, b_sizes, total


def semantic_scores(counts) -> dict:
    """Foreground overlap: Dice, IoU, precision, recall.

    Returns ``None`` for every metric when both volumes are entirely
    background — there is genuinely nothing to score, and reporting ``0.0``
    would read as "completely wrong" rather than "nothing there".
    """
    true_positive = sum(n for (a, b), n in counts.items() if a > 0 and b > 0)
    false_positive = sum(n for (a, b), n in counts.items() if a > 0 and b == 0)
    false_negative = sum(n for (a, b), n in counts.items() if a == 0 and b > 0)

    if true_positive + false_positive + false_negative == 0:
        return {"dice": None, "iou": None, "precision": None, "recall": None}

    denominator = 2 * true_positive + false_positive + false_negative
    union = true_positive + false_positive + false_negative
    predicted = true_positive + false_positive
    actual = true_positive + false_negative
    return {
        "dice": (2 * true_positive / denominator) if denominator else None,
        "iou": (true_positive / union) if union else None,
        "precision": (true_positive / predicted) if predicted else None,
        "recall": (true_positive / actual) if actual else None,
    }


def instance_scores(counts) -> dict:
    """Instance matching, plus false merges and splits.

    * **match** — a candidate and a reference instance whose IoU is strictly
      greater than :data:`MATCH_IOU`, which makes the assignment one-to-one by
      construction. A reference object split cleanly in two therefore scores
      zero matches and one false split, rather than two matches.
    * **false split** — one reference object covered by more than one
      candidate object (the annotator cut a mitochondrion in two).
    * **false merge** — one candidate object covering more than one reference
      object (the annotator fused two mitochondria into one id).

    Both counts use :data:`SIGNIFICANT_OVERLAP` so a few stray boundary voxels
    do not register as a whole extra object.
    """
    a_sizes, b_sizes, _ = _marginals(counts)
    a_instances = {a for a in a_sizes if a > 0}
    b_instances = {b for b in b_sizes if b > 0}

    if not a_instances and not b_instances:
        return {
            "instance_f1": None,
            "matched": 0,
            "candidate_instances": 0,
            "reference_instances": 0,
            "false_merges": None,
            "false_splits": None,
        }

    matches = 0
    # How many *significant* partners each instance has on the other side.
    partners_of_a: dict[int, int] = defaultdict(int)
    partners_of_b: dict[int, int] = defaultdict(int)

    for (a, b), shared in counts.items():
        if a <= 0 or b <= 0:
            continue
        union = a_sizes[a] + b_sizes[b] - shared
        if union > 0 and shared / union > MATCH_IOU:
            matches += 1
        smaller = min(a_sizes[a], b_sizes[b])
        if smaller > 0 and shared / smaller >= SIGNIFICANT_OVERLAP:
            partners_of_a[a] += 1
            partners_of_b[b] += 1

    false_merges = sum(max(0, n - 1) for n in partners_of_a.values())
    false_splits = sum(max(0, n - 1) for n in partners_of_b.values())

    total_instances = len(a_instances) + len(b_instances)
    return {
        "instance_f1": (2 * matches / total_instances) if total_instances else None,
        "matched": matches,
        "candidate_instances": len(a_instances),
        "reference_instances": len(b_instances),
        "false_merges": false_merges,
        "false_splits": false_splits,
    }


def variation_of_information(counts) -> float | None:
    """VI = H(A|B) + H(B|A), in nats. Lower is better; 0 is identical.

    Computed over **foreground voxels only**. Including background would let a
    mostly-empty volume score well simply for agreeing about emptiness, which
    is the opposite of informative for sparse organelle segmentation.
    """
    foreground = {(a, b): n for (a, b), n in counts.items() if a > 0 and b > 0}
    total = sum(foreground.values())
    if total == 0:
        return None

    a_sizes: dict[int, int] = defaultdict(int)
    b_sizes: dict[int, int] = defaultdict(int)
    for (a, b), n in foreground.items():
        a_sizes[a] += n
        b_sizes[b] += n

    conditional_a = 0.0  # H(A|B)
    conditional_b = 0.0  # H(B|A)
    for (a, b), n in foreground.items():
        joint = n / total
        conditional_a -= joint * math.log(n / b_sizes[b])
        conditional_b -= joint * math.log(n / a_sizes[a])
    return conditional_a + conditional_b


def compare(slab_pairs) -> dict:
    """Every metric for one candidate/reference pair, from one read."""
    counts = contingency(slab_pairs)
    scores = semantic_scores(counts)
    scores.update(instance_scores(counts))
    scores["variation_of_information"] = variation_of_information(counts)
    return scores
