"""Merge two labels into the smaller id across the whole volume.

UI contract: the annotator picks two label ids; voxels of the *larger* id
are rewritten to the *smaller* id. Argument order does not matter.

Persistence / metadata live in ``annotation/services.py:run_merge_labels_task``.
"""

from __future__ import annotations

import numpy as np


class MergeLabelsError(ValueError):
    pass


def run_merge_labels(mask: np.ndarray, label_a: int, label_b: int) -> dict:
    """Merge ``label_a`` and ``label_b`` in place, keeping ``min(a, b)``.

    Returns ``{"kept_label", "removed_label", "voxels_merged"}``.
    """
    a = int(label_a)
    b = int(label_b)
    if a < 1 or b < 1:
        raise MergeLabelsError("Both label ids must be positive integers.")
    if a == b:
        raise MergeLabelsError("The two label ids must be different.")

    kept = a if a < b else b
    removed = b if a < b else a

    drop_mask = mask == removed
    voxels_merged = int(np.count_nonzero(drop_mask))
    if voxels_merged == 0:
        raise MergeLabelsError(f"Label {removed} is not present in the volume.")
    if int(np.count_nonzero(mask == kept)) == 0:
        raise MergeLabelsError(f"Label {kept} is not present in the volume.")

    mask[drop_mask] = kept
    return {
        "kept_label": kept,
        "removed_label": removed,
        "voxels_merged": voxels_merged,
    }
