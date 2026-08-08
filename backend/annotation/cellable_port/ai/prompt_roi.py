"""Pure prompt-centred ROI geometry for interactive segmentation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RoiWindow:
    """Half-open ``[y0:y1, x0:x1]`` crop in full-slice coordinates."""

    y0: int
    y1: int
    x0: int
    x1: int

    def __post_init__(self):
        if self.y1 <= self.y0 or self.x1 <= self.x0:
            raise ValueError(f"Empty ROI: {self}")

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    def covers(self, height: int, width: int) -> bool:
        return (self.y0, self.y1, self.x0, self.x1) == (0, height, 0, width)

    def cache_token(self) -> str:
        return f"y{self.y0}-{self.y1}_x{self.x0}-{self.x1}"

    def crop(self, image: np.ndarray) -> np.ndarray:
        return np.asarray(image)[self.y0 : self.y1, self.x0 : self.x1]

    def paste(self, full_shape: tuple[int, int], crop_mask: np.ndarray) -> np.ndarray:
        mask = np.asarray(crop_mask, dtype=bool)
        if mask.shape != self.shape:
            raise ValueError(
                f"Crop mask shape {mask.shape} does not match ROI {self.shape}"
            )
        out = np.zeros(full_shape, dtype=bool)
        out[self.y0 : self.y1, self.x0 : self.x1] = mask
        return out

    def remap_points(self, points) -> list[list[float]]:
        return [[float(x) - self.x0, float(y) - self.y0] for x, y in points]


def _setting(name: str, default: int) -> int:
    try:
        from django.conf import settings

        return int(getattr(settings, name, default))
    except Exception:
        return default


def _clamp_window(
    cy: float,
    cx: float,
    side_y: int,
    side_x: int,
    height: int,
    width: int,
) -> RoiWindow:
    side_y = max(1, min(int(side_y), height))
    side_x = max(1, min(int(side_x), width))
    y0 = max(0, min(int(round(cy - side_y / 2)), height - side_y))
    x0 = max(0, min(int(round(cx - side_x / 2)), width - side_x))
    return RoiWindow(y0, y0 + side_y, x0, x0 + side_x)


def _snap(roi: RoiWindow, height: int, width: int, quantum: int) -> RoiWindow:
    if quantum <= 1:
        return roi
    y0 = max(0, min((roi.y0 // quantum) * quantum, height - roi.height))
    x0 = max(0, min((roi.x0 // quantum) * quantum, width - roi.width))
    return RoiWindow(y0, y0 + roi.height, x0, x0 + roi.width)


def compute_prompt_roi(
    height: int,
    width: int,
    *,
    points=None,
    box=None,
    target_size: int | None = None,
    max_size: int | None = None,
    point_pad: int | None = None,
    box_pad: int | None = None,
    snap: int | None = None,
) -> RoiWindow:
    """Plan a stable prompt-centred crop, retaining full-frame small images."""
    if height <= 0 or width <= 0:
        raise ValueError(f"Invalid image shape {(height, width)}")
    target = max(
        64,
        int(
            target_size
            if target_size is not None
            else _setting("MITO_AI_ROI_TARGET_SIZE", 1024)
        ),
    )
    maximum = max(
        target,
        int(
            max_size
            if max_size is not None
            else _setting("MITO_AI_ROI_MAX_SIZE", 1536)
        ),
    )
    point_margin = max(
        0,
        int(
            point_pad
            if point_pad is not None
            else _setting("MITO_AI_ROI_POINT_PAD", 256)
        ),
    )
    box_margin = max(
        0,
        int(
            box_pad if box_pad is not None else _setting("MITO_AI_ROI_BOX_PAD", 64)
        ),
    )
    quantum = max(
        1,
        int(snap if snap is not None else _setting("MITO_AI_ROI_SNAP", 64)),
    )
    if max(height, width) <= target:
        return RoiWindow(0, height, 0, width)

    prompts = list(points or []) + list(box or [])
    if not prompts:
        raise ValueError("ROI requires at least one point or box corner")
    coords = [(float(x), float(y)) for x, y in prompts]

    # Anchor common hover/click and small-box sequences on the first prompt.
    anchor_x, anchor_y = coords[0]
    anchored = _snap(
        _clamp_window(
            anchor_y,
            anchor_x,
            min(target, height),
            min(target, width),
            height,
            width,
        ),
        height,
        width,
        quantum,
    )
    if all(anchored.x0 <= x < anchored.x1 and anchored.y0 <= y < anchored.y1 for x, y in coords):
        return anchored

    xs, ys = zip(*coords)
    margin = box_margin if box and not points else point_margin
    needed = max(
        target,
        int(np.ceil(max(xs) - min(xs) + 2 * margin)),
        int(np.ceil(max(ys) - min(ys) + 2 * margin)),
    )
    side = min(maximum, needed)
    roi = _clamp_window(
        (min(ys) + max(ys)) / 2,
        (min(xs) + max(xs)) / 2,
        min(side, height),
        min(side, width),
        height,
        width,
    )
    return _snap(roi, height, width, quantum)


def encode_roi_bool_rle(
    full_shape: tuple[int, int],
    roi: RoiWindow,
    crop_mask: np.ndarray,
) -> list[list[int]]:
    """Encode a crop as full-plane RLE without allocating the full raster."""
    height, width = map(int, full_shape)
    mask = np.asarray(crop_mask, dtype=bool)
    if mask.shape != roi.shape:
        raise ValueError(f"Crop mask shape {mask.shape} does not match ROI {roi.shape}")
    if not (0 <= roi.y0 < roi.y1 <= height and 0 <= roi.x0 < roi.x1 <= width):
        raise ValueError(f"ROI {roi} is outside full shape {full_shape}")
    runs: list[list[int]] = []

    def append(value: int, count: int) -> None:
        if count <= 0:
            return
        if runs and runs[-1][0] == value:
            runs[-1][1] += int(count)
        else:
            runs.append([int(value), int(count)])

    append(0, roi.y0 * width)
    for row in mask:
        append(0, roi.x0)
        changes = np.flatnonzero(np.diff(row.astype(np.int8, copy=False))) + 1
        starts = np.concatenate(([0], changes))
        ends = np.concatenate((changes, [row.size]))
        for start, end in zip(starts, ends):
            append(int(row[int(start)]), int(end - start))
        append(0, width - roi.x1)
    append(0, (height - roi.y1) * width)
    return runs
