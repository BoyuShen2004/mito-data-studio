"""Per-instance size and skeleton-length measurements for a label volume.

Skeleton length is the TEASAR (kimimaro) cable length in physical units:
the sum of Euclidean edge lengths over the skeleton, with voxel size applied
once, in the array's (Z, Y, X) axis order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from annotation.cellable_port.labels_3d import label_summary
from annotation.third_party.em_erl_skel import skel_to_length, vol_to_skel
from annotation.visualization.slice_io import open_label_volume_readonly

CROP_PAD = 2
DUST_SIZE = 100


class MeasurementError(ValueError):
    """Raised when a volume cannot be measured (missing file, bad voxel size)."""


@dataclass(frozen=True)
class MitoMeasurement:
    label_id: int
    voxel_count: int
    volume_um3: float
    skeleton_length_um: float


def validate_voxel_size(voxel_size_zyx) -> tuple[float, float, float]:
    if voxel_size_zyx is None or len(voxel_size_zyx) != 3:
        raise MeasurementError("voxel size must be three values (z, y, x) in nm")
    if any(v is None for v in voxel_size_zyx):
        raise MeasurementError(f"voxel size is not fully set: (z, y, x) = {tuple(voxel_size_zyx)}")
    values = tuple(float(v) for v in voxel_size_zyx)
    if not all(math.isfinite(v) and v > 0 for v in values):
        raise MeasurementError(f"voxel size must be positive: (z, y, x) = {values}")
    return values


def skeleton_length_nm(mask: np.ndarray, voxel_size_zyx, *, dust_size: int = DUST_SIZE) -> float:
    """Cable length in nm of the skeleton of a binary (Z, Y, X) mask."""
    res = validate_voxel_size(voxel_size_zyx)
    labels = (np.asarray(mask) > 0).astype(np.uint8)
    skels = vol_to_skel(labels, obj_ids=[1], res=res, dust_size=dust_size, progress=False)
    if not skels:
        return 0.0
    rows = skel_to_length(skels, res=[1, 1, 1])
    return float(rows[0, 1])


def measure_label_volume(path, voxel_size_zyx, *, dust_size: int = DUST_SIZE) -> list[MitoMeasurement]:
    """Measure every nonzero instance in the label volume at ``path``."""
    path = Path(path)
    if not path.exists():
        raise MeasurementError(f"label file not found: {path}")
    vz, vy, vx = validate_voxel_size(voxel_size_zyx)
    voxel_nm3 = vz * vy * vx

    summary = label_summary(path)
    volume = open_label_volume_readonly(path)
    nz, ny, nx = volume.shape

    results = []
    for entry in summary["labels"]:
        lid = int(entry["id"])
        if lid == 0:
            continue
        count = int(entry["voxel_count"])
        z1, z2, y1, y2, x1, x2 = summary["bboxes"][lid]
        crop = np.asarray(
            volume[
                max(0, z1 - CROP_PAD) : min(nz, z2 + CROP_PAD),
                max(0, y1 - CROP_PAD) : min(ny, y2 + CROP_PAD),
                max(0, x1 - CROP_PAD) : min(nx, x2 + CROP_PAD),
            ]
        )
        length_nm = skeleton_length_nm(crop == lid, (vz, vy, vx), dust_size=dust_size)
        results.append(
            MitoMeasurement(
                label_id=lid,
                voxel_count=count,
                volume_um3=count * voxel_nm3 / 1e9,
                skeleton_length_um=length_nm / 1000.0,
            )
        )
    return results
