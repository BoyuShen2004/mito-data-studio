"""Region-of-interest guards shared by every annotation write path.

Every brush stroke, flood, interpolation and track write that runs in ROI-only
mode passes through here, so opening the mask file is on the hot path. The one
case where that read is provably pointless — a mask whose cached coverage is
exactly 0, i.e. nothing is inside the ROI — is short-circuited instead: no write
can land, so the answer is the original data, and reading a multi-gigabyte file
to learn that would be the expensive way to say nothing changed.
"""

from __future__ import annotations

import numpy as np


def request_roi_only(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _known_empty(volume) -> bool:
    """True only when coverage was measured *and* is zero.

    ``None`` means "not measured yet", which is not "empty" — guessing either
    way from an absent number is how a guard silently stops guarding.
    """
    coverage = getattr(volume, "region_mask_coverage", None)
    return coverage is not None and float(coverage) == 0.0


def region_mask_array(volume, expected_shape=None) -> np.ndarray:
    """Materialise the immutable ROI as bool for rare whole-volume tools."""
    from .visualization.slice_io import _open_volume, resolve_path

    if not volume.region_mask_location:
        raise ValueError("ROI-only mode requires a region mask.")
    if _known_empty(volume) and expected_shape is not None:
        return np.zeros(tuple(expected_shape), dtype=bool)
    mask = np.asarray(_open_volume(resolve_path(volume.region_mask_location))) != 0
    if expected_shape is not None and tuple(mask.shape) != tuple(expected_shape):
        raise ValueError(
            f"Region mask shape {tuple(mask.shape)} does not match label shape "
            f"{tuple(expected_shape)}."
        )
    return mask


def region_mask_slice(volume, axis: str, index: int, expected_shape=None) -> np.ndarray:
    """Read one bool ROI plane without scanning/materialising the full mask."""
    from .visualization.slice_io import read_slice

    if not volume.region_mask_location:
        raise ValueError("ROI-only mode requires a region mask.")
    if _known_empty(volume) and expected_shape is not None:
        return np.zeros(tuple(expected_shape), dtype=bool)
    mask = np.asarray(read_slice(volume.region_mask_location, axis, int(index))) != 0
    if expected_shape is not None and tuple(mask.shape) != tuple(expected_shape):
        raise ValueError(
            f"Region mask slice shape {tuple(mask.shape)} does not match label "
            f"slice shape {tuple(expected_shape)}."
        )
    return mask


def protect_slice_outside_roi(volume, axis: str, index: int, before, proposed):
    """Return proposed values inside ROI and byte-for-byte originals outside."""
    roi = region_mask_slice(volume, axis, index, np.shape(before))
    return np.where(roi, proposed, before)


def protect_volume_outside_roi(volume, before, proposed):
    roi = region_mask_array(volume, np.shape(before))
    proposed[~roi] = before[~roi]
    return proposed


# --- Volume-wide "Region only" membership ---------------------------------
#
# "Region only" shows an instance whole if it touches the ROI, and hides it if
# it does not. That is a property of the *instance*, so it has to be decided
# over the whole volume: a mitochondrion 40 planes long that enters the ROI on
# five of them is one object, and hiding it on the other thirty-five (which is
# what a per-plane decision does) shows the annotator a broken mito.
#
# Answering it naively means reading the whole label volume. Two things keep
# that off the scrub path:
#
# * **Crop to the ROI's own bounding box.** Nothing outside it can contribute,
#   and an ROI is typically a small fraction of the volume. The box itself is
#   derived from the *immutable* region mask, so it is cached for the life of
#   the process.
# * **Cache the answer per (label file, region file) mtime pair**, exactly like
#   ``labels_3d.label_summary``. Scrubbing does not change either mtime, so it
#   never rescans; a Save or a reset does, and pays for one scan.
#
# Chunking is by *bytes*, so peak memory is the same on a 4.5k x 3.9k volume as
# on a small one.

_TARGET_SCAN_BYTES = 64 << 20
# {region path: (mtime, bbox or None)} — the ROI never changes, so this is
# effectively permanent; it is keyed by mtime anyway so a re-registered mask
# cannot be answered for out of a stale entry.
_roi_bbox_cache: dict[str, tuple[float, tuple | None]] = {}
# {(label path, region path): (label mtime, region mtime, frozenset of ids)}
_roi_ids_cache: dict[tuple[str, str], tuple[float, float, frozenset]] = {}


def forget_region_label_ids(label_path=None) -> None:
    """Drop cached membership. Called where a mask is replaced wholesale (reset)
    rather than written plane by plane, since those paths can leave the mtime
    unchanged on a coarse-resolution filesystem."""
    if label_path is None:
        _roi_ids_cache.clear()
        return
    wanted = str(label_path)
    for key in [key for key in _roi_ids_cache if key[0] == wanted]:
        _roi_ids_cache.pop(key, None)


def _chunk_size(shape, itemsize: int) -> int:
    per_plane = max(1, int(np.prod(shape[1:])) * max(1, itemsize))
    return max(1, min(int(shape[0]), _TARGET_SCAN_BYTES // per_plane))


def _consecutive_runs(planes) -> list[tuple[int, int]]:
    """Group sorted plane indices into ``(start, stop)`` half-open runs, so a
    block of adjacent planes is one contiguous read rather than N."""
    runs: list[tuple[int, int]] = []
    for z in planes:
        if runs and z == runs[-1][1]:
            runs[-1] = (runs[-1][0], z + 1)
        else:
            runs.append((z, z + 1))
    return runs


def _roi_extent(region_path) -> tuple | None:
    """``((y0, y1, x0, x1), (z, ...))`` — the ROI's in-plane bounding box and
    the exact planes that contain any of it. ``None`` when the mask is empty.

    The plane *list*, not just a z range, is what lets the scan skip planes the
    ROI does not reach at all: an ROI is often a handful of blocks scattered
    through z, and reading the gaps between them is most of the work."""
    from .visualization.slice_io import _open_volume

    key = str(region_path)
    mtime = region_path.stat().st_mtime
    cached = _roi_bbox_cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    mask = _open_volume(region_path)
    step = _chunk_size(mask.shape, getattr(mask, "dtype", np.dtype("u1")).itemsize)
    zs: list[int] = []
    any_y = np.zeros(int(mask.shape[1]), dtype=bool)
    any_x = np.zeros(int(mask.shape[2]), dtype=bool)
    for z0 in range(0, int(mask.shape[0]), step):
        chunk = np.asarray(mask[z0 : z0 + step]) != 0
        if not chunk.any():
            continue
        planes = np.nonzero(chunk.any(axis=(1, 2)))[0]
        zs.extend((z0 + int(offset)) for offset in planes)
        any_y |= chunk.any(axis=(0, 2))
        any_x |= chunk.any(axis=(0, 1))

    if not zs:
        extent = None
    else:
        ys = np.nonzero(any_y)[0]
        xs = np.nonzero(any_x)[0]
        extent = (
            (int(ys[0]), int(ys[-1]) + 1, int(xs[0]), int(xs[-1]) + 1),
            tuple(sorted(zs)),
        )
    _roi_bbox_cache[key] = (mtime, extent)
    return extent


def region_touching_label_ids(label_path, region_path) -> frozenset[int]:
    """Every instance id with at least one voxel inside the ROI, anywhere in z.

    Both arguments are resolved ``Path``s. Raises ``ValueError`` when the two
    volumes disagree on shape — the caller turns that into a 400 rather than
    filtering by a membership set that means nothing.
    """
    from .visualization.slice_io import _open_volume, open_label_volume_readonly

    if not label_path.exists() or not region_path.exists():
        return frozenset()
    key = (str(label_path), str(region_path))
    label_mtime = label_path.stat().st_mtime
    region_mtime = region_path.stat().st_mtime
    cached = _roi_ids_cache.get(key)
    if cached is not None and cached[0] == label_mtime and cached[1] == region_mtime:
        return cached[2]

    labels = open_label_volume_readonly(label_path)
    mask = _open_volume(region_path)
    if tuple(labels.shape) != tuple(mask.shape):
        raise ValueError(
            f"Region mask shape {tuple(mask.shape)} does not match label shape "
            f"{tuple(labels.shape)}."
        )
    extent = _roi_extent(region_path)
    if extent is None:
        found: frozenset[int] = frozenset()
    else:
        (y0, y1, x0, x1), planes = extent
        step = _chunk_size(labels.shape, getattr(labels, "dtype", np.dtype("i4")).itemsize)
        ids: set[int] = set()
        # Read **whole planes** and crop in RAM. Slicing `[z, y0:y1, x0:x1]`
        # straight out of the memmap looks like it reads less, but it walks the
        # file in strided fragments: measured 21s that way against ~1s reading
        # the same planes contiguously, on a 256x2048x2048 volume whose ROI
        # covers 8% of the voxels. The crop is free once the plane is in memory.
        for offset in range(0, len(planes), step):
            block = planes[offset : offset + step]
            # Consecutive planes read as one slice; a gap starts a new read, so
            # the planes the ROI never reaches are never touched.
            for start, stop in _consecutive_runs(block):
                inside = np.asarray(mask[start:stop])[:, y0:y1, x0:x1] != 0
                if not inside.any():
                    continue
                plane = np.asarray(labels[start:stop])[:, y0:y1, x0:x1]
                present = np.unique(plane[inside])
                ids.update(int(value) for value in present.tolist() if value > 0)
        found = frozenset(ids)

    _roi_ids_cache[key] = (label_mtime, region_mtime, found)
    return found
