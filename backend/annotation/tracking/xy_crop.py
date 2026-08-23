"""XY crop/paste helpers for SAM2 tracking on large EM planes."""

from __future__ import annotations

import numpy as np
from django.conf import settings

from annotation.cellable_port.ai.prompt_roi import RoiWindow


def plan_xy_roi(seeds, height: int, width: int) -> RoiWindow:
    masks = [np.asarray(mask, dtype=bool) for per_z in seeds.values() for mask in per_z.values()]
    nonempty = [mask for mask in masks if mask.any()]
    if not nonempty:
        return RoiWindow(0, height, 0, width)
    if any(mask.shape != (height, width) for mask in nonempty):
        raise ValueError("Seed mask shape does not match image plane")
    ys, xs = zip(*(np.nonzero(mask) for mask in nonempty))
    y0 = min(int(values.min()) for values in ys)
    y1 = max(int(values.max()) for values in ys) + 1
    x0 = min(int(values.min()) for values in xs)
    x1 = max(int(values.max()) for values in xs) + 1
    pad = max(0, int(getattr(settings, "MITO_SAM2_XY_PAD", 256)))
    minimum = max(1, int(getattr(settings, "MITO_SAM2_XY_MIN", 512)))
    maximum = max(minimum, int(getattr(settings, "MITO_SAM2_XY_MAX", 2048)))
    side = min(maximum, max(minimum, y1 - y0 + 2 * pad, x1 - x0 + 2 * pad))
    side_y, side_x = min(side, height), min(side, width)
    cy, cx = (y0 + y1) / 2, (x0 + x1) / 2
    origin_y = max(0, min(int(round(cy - side_y / 2)), height - side_y))
    origin_x = max(0, min(int(round(cx - side_x / 2)), width - side_x))
    return RoiWindow(origin_y, origin_y + side_y, origin_x, origin_x + side_x)


def _xy_max() -> int:
    minimum = max(1, int(getattr(settings, "MITO_SAM2_XY_MIN", 512)))
    return max(minimum, int(getattr(settings, "MITO_SAM2_XY_MAX", 2048)))


def _seed_bbox(per_z):
    """``(y0, y1, x0, x1)`` covering one branch's seeds, or ``None`` if empty."""
    boxes = []
    for mask in per_z.values():
        mask = np.asarray(mask, dtype=bool)
        if not mask.any():
            continue
        ys, xs = np.nonzero(mask)
        boxes.append((int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1))
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes), max(b[1] for b in boxes),
        min(b[2] for b in boxes), max(b[3] for b in boxes),
    )


def cluster_seeds(seeds, height: int, width: int):
    """Split ``seeds`` into groups that each fit inside one crop window.

    The crop is a square capped at ``MITO_SAM2_XY_MAX``, centred on the seeds it
    is planned for. On a plane wider than that cap — and the production planes
    are thousands of pixels across — two prompts far enough apart cannot share
    one window: cropping to their combined bounding box silently reduced the
    outlying seed to an empty mask, so its branch was propagated as nothing and
    the annotator's second prompt simply disappeared.

    Grouping instead of stretching also keeps propagation quick: each window is
    only as large as the prompts inside it need, rather than every prompt paying
    for the distance to the furthest one.
    """
    limit = min(_xy_max(), max(height, width))
    boxes = {}
    empty = {}
    for branch, per_z in seeds.items():
        box = _seed_bbox(per_z)
        if box is None:
            empty[branch] = per_z
        else:
            boxes[branch] = box

    clusters: list[dict] = []
    # Deterministic order: identical input must produce identical grouping, so
    # branch ids and audit metadata stay stable across reruns.
    for branch in sorted(boxes, key=lambda b: (boxes[b][0], boxes[b][2], int(b))):
        box = boxes[branch]
        for cluster in clusters:
            merged = (
                min(cluster["box"][0], box[0]), max(cluster["box"][1], box[1]),
                min(cluster["box"][2], box[2]), max(cluster["box"][3], box[3]),
            )
            if max(merged[1] - merged[0], merged[3] - merged[2]) <= limit:
                cluster["box"] = merged
                cluster["branches"].append(branch)
                break
        else:
            clusters.append({"box": box, "branches": [branch]})

    groups = [{b: seeds[b] for b in cluster["branches"]} for cluster in clusters]
    if empty:
        # Nothing to track, but the branch must still round-trip so the caller's
        # bookkeeping keeps its shape.
        if groups:
            groups[0].update(empty)
        else:
            groups.append(empty)
    return groups


def crop_stack(stack: np.ndarray, roi: RoiWindow) -> np.ndarray:
    return np.asarray(stack)[:, roi.y0 : roi.y1, roi.x0 : roi.x1]


def crop_seeds(seeds, roi: RoiWindow):
    return {
        branch: {
            z: np.asarray(mask, dtype=bool)[roi.y0 : roi.y1, roi.x0 : roi.x1]
            for z, mask in per_z.items()
        }
        for branch, per_z in seeds.items()
    }


def paste_masks(masks, roi: RoiWindow, full_shape_yx: tuple[int, int]):
    return {
        branch: {
            z: roi.paste(full_shape_yx, np.asarray(mask, dtype=bool))
            for z, mask in per_z.items()
        }
        for branch, per_z in masks.items()
    }


def _touches_border(mask: np.ndarray) -> bool:
    mask = np.asarray(mask, dtype=bool)
    return bool(
        mask.any()
        and (
            mask[0].any()
            or mask[-1].any()
            or mask[:, 0].any()
            or mask[:, -1].any()
        )
    )


def maybe_expand_for_border(roi, height: int, width: int, masks):
    if roi.covers(height, width):
        return None
    if not any(_touches_border(mask) for per_z in masks.values() for mask in per_z.values()):
        return None
    maximum = max(1, int(getattr(settings, "MITO_SAM2_XY_MAX", 2048)))
    side_y = min(height, maximum, max(roi.height + 1, int(np.ceil(roi.height * 1.6))))
    side_x = min(width, maximum, max(roi.width + 1, int(np.ceil(roi.width * 1.6))))
    cy, cx = (roi.y0 + roi.y1) / 2, (roi.x0 + roi.x1) / 2
    y0 = max(0, min(int(round(cy - side_y / 2)), height - side_y))
    x0 = max(0, min(int(round(cx - side_x / 2)), width - side_x))
    expanded = RoiWindow(y0, y0 + side_y, x0, x0 + side_x)
    return None if expanded == roi else expanded
