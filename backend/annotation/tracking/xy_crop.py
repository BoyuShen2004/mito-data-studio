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
