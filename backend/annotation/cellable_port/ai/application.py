"""Single application boundary for interactive EfficientSAM inference."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
from pathlib import Path
import time

import numpy as np
from django.conf import settings

from annotation.label_paths import volume_embeddings_dir_rel_path, working_mask_stem
from annotation.visualization.slice_io import AXES, read_slice, resolve_path

from . import embed_cache
from .normalize import normalize_for_ai
from .prompt_roi import RoiWindow, compute_prompt_roi, encode_roi_bool_rle
from .registry import get_efficient_sam

_timing_log = logging.getLogger("mito.ai.timing")


@dataclass(frozen=True)
class PreparedSlice:
    full_shape: tuple[int, int]
    roi: RoiWindow
    image: np.ndarray
    cache_path: Path | None

    def encode_mask(self, mask: np.ndarray) -> list[list[int]]:
        return encode_roi_bool_rle(self.full_shape, self.roi, mask)


def embedding_cache_path(
    volume,
    axis: str,
    index: int,
    *,
    roi_token: str | None = None,
) -> Path | None:
    if not volume.image_location:
        return None
    image_path = resolve_path(volume.image_location)
    try:
        image_mtime = image_path.stat().st_mtime
    except OSError:
        image_mtime = 0.0
    return embed_cache.cache_path_for(
        volume_embeddings_dir_rel_path(volume),
        working_mask_stem(volume),
        axis,
        index,
        getattr(settings, "MITO_EFFICIENT_SAM_VARIANT", "vits"),
        image_mtime,
        roi_token=roi_token,
    )


def _validate_coordinates(values, name: str, height: int, width: int) -> None:
    if values is None:
        return
    for value in values:
        try:
            x, y = map(float, value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Each {name} must be [x, y].") from exc
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError(f"Each {name} coordinate must be finite.")
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(
                f"{name.title()} {[x, y]} is outside image shape {(height, width)}."
            )


def _prepare_array(volume, axis: str, index: int, raw, *, points=None, box=None):
    height, width = raw.shape[:2]
    _validate_coordinates(points, "point", height, width)
    _validate_coordinates(box, "box corner", height, width)
    roi = compute_prompt_roi(height, width, points=points, box=box)
    token = None if roi.covers(height, width) else roi.cache_token()
    return PreparedSlice(
        (height, width),
        roi,
        normalize_for_ai(roi.crop(raw)),
        embedding_cache_path(volume, axis, index, roi_token=token),
    )


def _prepare(volume, axis: str, index: int, *, points=None, box=None):
    return _prepare_array(
        volume,
        axis,
        index,
        read_slice(volume.image_location, axis, index),
        points=points,
        box=box,
    )


def predict_mask(
    task,
    axis: str,
    index: int,
    mode: str,
    *,
    points=None,
    point_labels=None,
    box=None,
) -> dict:
    volume = task.volume
    if axis not in AXES:
        raise ValueError(f"Unknown axis '{axis}'.")
    if not volume.image_location:
        raise ValueError("Volume has no image.")
    if mode not in {"points", "box", "boundary"}:
        raise ValueError(f"Unknown predict mode '{mode}'.")

    timed = bool(getattr(settings, "MITO_AI_TIMING", False))
    total_started = time.perf_counter() if timed else 0.0
    prepare_started = time.perf_counter() if timed else 0.0
    prepared = _prepare(volume, axis, index, points=points, box=box)
    if timed:
        _timing_log.info(
            "prepare mode=%s axis=%s index=%d roi=%s %.1fms",
            mode,
            axis,
            index,
            prepared.roi.cache_token(),
            (time.perf_counter() - prepare_started) * 1000.0,
        )
    model = get_efficient_sam()
    if mode in {"points", "boundary"}:
        mask = model.predict_mask_from_points(
            prepared.image,
            prepared.roi.remap_points(points),
            point_labels,
            disk_path=prepared.cache_path,
        )
    else:
        mask = model.predict_mask_from_box(
            prepared.image,
            prepared.roi.remap_points(box),
            disk_path=prepared.cache_path,
        )
    mask = np.asarray(mask, dtype=bool)
    if mode == "boundary" and mask.any():
        import scipy.ndimage as ndi

        mask = ndi.binary_dilation(mask, iterations=3) ^ ndi.binary_erosion(mask)
    result = {"shape": list(prepared.full_shape), "runs": prepared.encode_mask(mask)}
    if timed:
        _timing_log.info(
            "predict total mode=%s axis=%s index=%d %.1fms",
            mode,
            axis,
            index,
            (time.perf_counter() - total_started) * 1000.0,
        )
    return result


def warm_embedding(task, axis: str, index: int, *, point=None) -> bool:
    volume = task.volume
    if axis not in AXES or not volume.image_location:
        return False
    timed = bool(getattr(settings, "MITO_AI_TIMING", False))
    started = time.perf_counter() if timed else 0.0
    raw = read_slice(volume.image_location, axis, index)
    height, width = raw.shape[:2]
    point = point if point is not None else [width // 2, height // 2]
    prepared = _prepare_array(volume, axis, index, raw, points=[point])
    get_efficient_sam().warm(prepared.image, disk_path=prepared.cache_path)
    if timed:
        _timing_log.info(
            "warm total axis=%s index=%d roi=%s %.1fms",
            axis,
            index,
            prepared.roi.cache_token(),
            (time.perf_counter() - started) * 1000.0,
        )
    return True
