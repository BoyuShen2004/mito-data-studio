"""Cached region-mask coverage using the same TIFF/HDF5/NIfTI IO as viewers."""

from __future__ import annotations

import threading
from collections import OrderedDict

import numpy as np


def calculate_region_mask_coverage(location: str, *, chunk_depth: int = 16) -> float:
    """Return ``nonzero voxels / all voxels`` without materialising a volume.

    The shared volume reader is lazy for every supported source format.  A
    bounded z slab is converted at a time, so registration/backfill remains
    format-neutral and memory use is independent of the full volume size.
    """
    from annotation.visualization.slice_io import _open_volume, resolve_path

    array = _open_volume(resolve_path(location))
    if array.ndim != 3 or any(int(size) <= 0 for size in array.shape):
        raise ValueError("Region mask must be a non-empty 3-D volume.")
    nonzero = 0
    total = 0
    step = max(1, int(chunk_depth))
    for start in range(0, int(array.shape[0]), step):
        slab = np.asarray(array[start : start + step])
        nonzero += int(np.count_nonzero(slab))
        total += int(slab.size)
    if total == 0:
        raise ValueError("Region mask has no voxels.")
    return nonzero / total


def refresh_region_mask_coverage(volume, *, save: bool = True) -> float | None:
    """Compute and cache one volume's coverage; no mask is an explicit null."""
    location = volume.region_mask_location
    coverage = calculate_region_mask_coverage(location) if location else None
    volume.region_mask_coverage = coverage
    if save:
        volume.save(update_fields=["region_mask_coverage"])
    return coverage


# --- Which planes actually contain region ----------------------------------
# "Jump to the nearest slice that has region" needs, per axis, the indices whose
# plane holds any nonzero ROI voxel. Answering it per request from the client
# would be one PNG fetch per candidate plane; answering it per request on the
# server would re-read the mask every time. So it is computed exactly once per
# (file, mtime) — in a single sequential slab pass that fills all three axes at
# once, the same bounded-memory loop `calculate_region_mask_coverage` uses — and
# memoized for the process. The mtime in the key is what makes a replaced mask
# recompute instead of serving the previous file's answer.

MAX_CACHED_REGION_INDEXES = 8

_index_cache: "OrderedDict[tuple, dict[str, list[int]]]" = OrderedDict()
# Held only for dict bookkeeping, never across a scan — a full pass over a
# 256x2048x2048 mask takes seconds, and holding one lock for it would stall
# every other volume's viewer behind an unrelated file.
_index_lock = threading.RLock()
# One lock per mask file instead: three viewers opening the *same* volume at
# once wait for one pass rather than starting three, which is the case worth
# serializing.
_index_scan_locks: dict[tuple, threading.Lock] = {}


def calculate_region_nonempty_indices(
    location: str, *, chunk_depth: int = 16
) -> dict[str, list[int]]:
    """Return ``{"z": [...], "y": [...], "x": [...]}`` of ROI-bearing planes.

    One pass over z slabs: a z index is kept when its plane has any nonzero
    voxel, while the y/x answers accumulate as running ORs of length Y and X.
    Memory is independent of the volume size, and the file is read once.
    """
    from annotation.visualization.slice_io import _open_volume, resolve_path

    array = _open_volume(resolve_path(location))
    if array.ndim != 3 or any(int(size) <= 0 for size in array.shape):
        raise ValueError("Region mask must be a non-empty 3-D volume.")
    depth, height, width = (int(size) for size in array.shape)
    z_hits: list[int] = []
    y_any = np.zeros(height, dtype=bool)
    x_any = np.zeros(width, dtype=bool)
    step = max(1, int(chunk_depth))
    for start in range(0, depth, step):
        slab = np.asarray(array[start : start + step]) != 0
        for offset in range(slab.shape[0]):
            if slab[offset].any():
                z_hits.append(start + offset)
        y_any |= slab.any(axis=(0, 2))
        x_any |= slab.any(axis=(0, 1))
    return {
        "z": z_hits,
        "y": [int(i) for i in np.flatnonzero(y_any)],
        "x": [int(i) for i in np.flatnonzero(x_any)],
    }


def region_nonempty_indices(volume, axis: str) -> list[int]:
    """Cached ROI-bearing plane indices for one axis of one volume.

    A coverage of exactly 0 is the one case that needs no file read at all —
    nothing is inside the ROI, so no plane can be. ``None`` coverage means "not
    measured", which is not "empty" (see `annotation.region_mask._known_empty`).
    """
    from annotation.visualization.slice_io import resolve_path

    if axis not in {"z", "y", "x"}:
        raise ValueError(f"Unknown axis {axis!r}. Use one of ['z', 'y', 'x'].")
    location = volume.region_mask_location
    if not location:
        raise ValueError("Volume has no region mask.")
    coverage = getattr(volume, "region_mask_coverage", None)
    if coverage is not None and float(coverage) == 0.0:
        return []
    path = resolve_path(location)
    key = (str(path), path.stat().st_mtime if path.exists() else 0)
    cached = _lookup(key)
    if cached is not None:
        return list(cached[axis])
    with _index_lock:
        scan_lock = _index_scan_locks.setdefault(key, threading.Lock())
    with scan_lock:
        # Another thread may have finished the scan while this one waited.
        cached = _lookup(key)
        if cached is None:
            try:
                cached = calculate_region_nonempty_indices(location)
            except Exception:
                # An unreadable mask caches nothing, so its lock would linger
                # for a scan that will never be retried under it.
                with _index_lock:
                    _index_scan_locks.pop(key, None)
                raise
            with _index_lock:
                _index_cache[key] = cached
                while len(_index_cache) > MAX_CACHED_REGION_INDEXES:
                    evicted, _ = _index_cache.popitem(last=False)
                    _index_scan_locks.pop(evicted, None)
    return list(cached[axis])


def _lookup(key) -> dict[str, list[int]] | None:
    with _index_lock:
        cached = _index_cache.get(key)
        if cached is not None:
            _index_cache.move_to_end(key)
        return cached


def clear_region_index_cache() -> None:
    """Drop the memoized scans (tests, and any deliberate cache reset)."""
    with _index_lock:
        _index_cache.clear()
        _index_scan_locks.clear()
