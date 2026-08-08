"""3D marker-based watershed for the Seeds tool.

Ported from ``cellable/labelme/app.py``: ``apply_3d_watershed`` +
``_label_bbox_3d`` + ``compute_bbox_3d``. This is the pure numpy/scipy/
skimage segmentation core — bbox-crop around the target label (so a whole
gigabyte-scale label volume is never processed at once), seed a
``skimage.segmentation.watershed`` on the cropped region's distance
transform, iteratively drop markers whose resulting region is too small,
then relabel: the largest resulting region keeps the original label id and
every other region gets a newly minted id. Qt-specific bookkeeping from the
original (statusbar messages, the watershed undo/redo stack, 3D-cache
invalidation, ``_registerAutoSegmentationLabels``) is stripped — the caller
(``annotation/services.py:run_watershed_task``) writes the mutated volume to
the working label copy the same way ``track_task_fork`` does.
"""

from __future__ import annotations

import numpy as np

MIN_REGION_SIZE = 50
MAX_ITERATIONS = 10


class WatershedError(ValueError):
    pass


def label_bbox_3d(mask: np.ndarray, label: int, padding: int = 0):
    """Return an exclusive bbox ``(z1, z2, y1, y2, x1, x2)`` for ``label``
    in ``mask``, padded and clamped to the volume, or ``None`` if the label
    isn't present. Ported from ``_label_bbox_3d``."""
    zs, ys, xs = np.nonzero(mask == int(label))
    if zs.size == 0:
        return None
    z1 = max(0, int(zs.min()) - padding)
    z2 = min(mask.shape[0], int(zs.max()) + padding + 1)
    y1 = max(0, int(ys.min()) - padding)
    y2 = min(mask.shape[1], int(ys.max()) + padding + 1)
    x1 = max(0, int(xs.min()) - padding)
    x2 = min(mask.shape[2], int(xs.max()) + padding + 1)
    return z1, z2, y1, y2, x1, x2


def run_watershed_3d(
    mask: np.ndarray,
    target_label: int,
    seeds_zyx,
    padding: int = 5,
    max_existing_label: int | None = None,
) -> dict:
    """Split ``target_label`` inside ``mask`` (mutated in place) using
    watershed seeded at ``seeds_zyx`` (an iterable of ``(z, y, x)`` voxel
    coordinates, all expected to fall on ``target_label``).

    Returns ``{"target_label": int, "new_label_ids": [...], "bbox": [...]}``.
    Unseeded, disconnected pieces of ``target_label`` outside any watershed
    basin are left as ``target_label`` (not cleared) — same guarantee the
    original had: watershed only labels components reachable from a marker.
    """
    import scipy.ndimage as ndi
    from skimage.segmentation import watershed

    bbox = label_bbox_3d(mask, target_label, padding=padding)
    if bbox is None:
        raise WatershedError(f"Label {target_label} not found in the volume.")
    z1, z2, y1, y2, x1, x2 = bbox

    mask_sub = mask[z1:z2, y1:y2, x1:x2]
    target_sub = mask_sub == target_label
    target_voxel_count = int(np.count_nonzero(target_sub))
    if target_voxel_count == 0:
        raise WatershedError(f"Label {target_label} not found in the volume.")

    markers_sub = np.zeros_like(target_sub, dtype=np.int32)
    placed = 0
    for i, (z, y, x) in enumerate(seeds_zyx):
        zs, ys, xs = int(z) - z1, int(y) - y1, int(x) - x1
        if (
            0 <= zs < target_sub.shape[0]
            and 0 <= ys < target_sub.shape[1]
            and 0 <= xs < target_sub.shape[2]
        ):
            markers_sub[zs, ys, xs] = i + 1
            placed += 1
    if placed == 0:
        raise WatershedError("None of the seed points fall inside this label's region.")

    distance_sub = ndi.distance_transform_edt(target_sub)
    ws_labels_sub = None
    for _iteration in range(MAX_ITERATIONS):
        ws_labels_sub = watershed(-distance_sub, markers_sub, mask=target_sub)
        region_sizes = np.bincount(ws_labels_sub.ravel())
        unique_regions = np.flatnonzero(region_sizes)
        unique_regions = unique_regions[unique_regions > 0]
        small_regions = unique_regions[region_sizes[unique_regions] < MIN_REGION_SIZE]
        if small_regions.size == 0:
            break
        markers_sub[np.isin(markers_sub, small_regions)] = 0

    region_sizes = np.bincount(ws_labels_sub.ravel())
    unique_regions = np.flatnonzero(region_sizes)
    unique_regions = unique_regions[unique_regions > 0]
    if unique_regions.size == 0:
        raise WatershedError(
            f"Watershed produced no regions for label {target_label}; left unchanged."
        )

    largest_region = int(unique_regions[np.argmax(region_sizes[unique_regions])])
    split_regions = unique_regions[unique_regions != largest_region]
    if max_existing_label is None:
        max_existing_label = int(mask.max())
    else:
        max_existing_label = int(max_existing_label)
    new_labels_array = np.arange(
        max_existing_label + 1,
        max_existing_label + split_regions.size + 1,
        dtype=mask.dtype,
    )
    region_to_label = np.zeros(int(ws_labels_sub.max()) + 1, dtype=mask.dtype)
    region_to_label[largest_region] = target_label
    region_to_label[split_regions] = new_labels_array
    relabeled_sub = region_to_label[ws_labels_sub]

    # Watershed only labels connected components reachable from a marker —
    # preserve unseeded disconnected pieces of the target label rather than
    # clearing the whole target region and accidentally deleting them.
    assigned = target_sub & (ws_labels_sub > 0)
    mask_sub[assigned] = relabeled_sub[assigned]

    return {
        "target_label": int(target_label),
        "new_label_ids": [int(v) for v in new_labels_array],
        "bbox": [int(z1), int(z2), int(y1), int(y2), int(x1), int(x2)],
    }
