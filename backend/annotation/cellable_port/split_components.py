"""3D connected-component split for the Split 3D tool.

Ported from ``cellable/labelme/app.py:split_label``: crop to the target
label's bbox, run 26-connected component labeling on the binary ROI, drop
components smaller than ``SIZE_THRESHOLD`` voxels (those voxels are cleared),
keep the largest remaining component as the original id, and assign new ids
to every other kept component.

Uses ``scipy.ndimage.label`` with a full 3x3x3 structuring element (same
26-connectivity as Cellable's ``cc3d.connected_components(..., connectivity=26)``)
so we stay on the same scipy stack already required by the Seeds watershed
path — no extra ``cc3d`` dependency.
"""

from __future__ import annotations

import numpy as np

from .watershed import label_bbox_3d

SIZE_THRESHOLD = 100


class SplitComponentsError(ValueError):
    pass


def run_split_components_3d(
    mask: np.ndarray,
    target_label: int,
    *,
    size_threshold: int = SIZE_THRESHOLD,
    padding: int = 0,
    max_existing_label: int | None = None,
) -> dict:
    """Split ``target_label`` inside ``mask`` (mutated in place) into its
    3D connected components.

    Returns ``{"target_label": int, "new_label_ids": [...], "bbox": [...],
    "components_kept": int, "voxels_cleared": int}``.
    """
    import scipy.ndimage as ndi

    bbox = label_bbox_3d(mask, target_label, padding=padding)
    if bbox is None:
        raise SplitComponentsError(f"Label {target_label} not found in the volume.")
    z1, z2, y1, y2, x1, x2 = bbox

    mask_roi = mask[z1:z2, y1:y2, x1:x2]
    target_roi = mask_roi == target_label
    target_voxel_count = int(np.count_nonzero(target_roi))
    if target_voxel_count == 0:
        raise SplitComponentsError(f"Label {target_label} not found in the volume.")

    # 26-connectivity — matches Cellable's cc3d connectivity=26.
    structure = np.ones((3, 3, 3), dtype=np.int8)
    cc_map, num_components = ndi.label(target_roi, structure=structure)

    if num_components > 0:
        voxel_counts = np.bincount(cc_map.ravel())
        keep_components = np.flatnonzero(voxel_counts >= int(size_threshold))
        keep_components = keep_components[keep_components > 0]
    else:
        voxel_counts = np.zeros(1, dtype=np.int64)
        keep_components = np.array([], dtype=np.int64)

    num_kept = int(keep_components.size)
    if num_kept == 0:
        # Cellable clears the whole target when nothing survives the size filter.
        mask_roi[target_roi] = 0
        return {
            "target_label": int(target_label),
            "new_label_ids": [],
            "bbox": [int(z1), int(z2), int(y1), int(y2), int(x1), int(x2)],
            "components_kept": 0,
            "voxels_cleared": target_voxel_count,
        }

    largest_component = int(keep_components[np.argmax(voxel_counts[keep_components])])
    split_components = keep_components[keep_components != largest_component]
    if max_existing_label is None:
        max_existing_label = int(mask.max())
    else:
        max_existing_label = int(max_existing_label)
    new_labels_array = np.arange(
        max_existing_label + 1,
        max_existing_label + split_components.size + 1,
        dtype=mask.dtype,
    )
    component_to_label = np.zeros(int(cc_map.max()) + 1, dtype=mask.dtype)
    component_to_label[largest_component] = target_label
    component_to_label[split_components] = new_labels_array

    relabeled_roi = component_to_label[cc_map]
    mask_roi[target_roi] = 0
    relabeled_pixels = relabeled_roi > 0
    mask_roi[relabeled_pixels] = relabeled_roi[relabeled_pixels]

    kept_voxels = int(np.count_nonzero(relabeled_pixels))
    return {
        "target_label": int(target_label),
        "new_label_ids": [int(v) for v in new_labels_array],
        "bbox": [int(z1), int(z2), int(y1), int(y2), int(x1), int(x2)],
        "components_kept": num_kept,
        "voxels_cleared": target_voxel_count - kept_voxels,
    }
