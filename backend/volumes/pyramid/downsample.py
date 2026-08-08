"""Reducing one pyramid level to the next.

Pure numpy: no Django, no zarr, no filesystem.

Two reductions, chosen by what the data *means*:

* **Image data → mean.** Averaging is the right answer for intensity: it is what
  a lower-resolution sensor would have recorded.
* **Label data → mode (most common non-zero).** Averaging label ids is
  meaningless — the mean of instance 3 and instance 7 is instance 5, which is a
  different object. Worse, it invents ids that never existed. So a label block
  takes the most common id in it, and background only wins when the block is
  entirely background, so a thin process does not vanish at the first level.

Bounded by construction: a caller reduces one z-slab at a time (see
:func:`reduce_slab`), so peak memory is a function of plane size and the z
factor, never of volume size. Phase 8 measured 423 ms dense versus 2.13 ms
bounded on a *single* 1024² plane; a multi-gigabyte volume cannot be loaded to
downsample itself.
"""

from __future__ import annotations

import numpy as np

#: How the values in a block should be combined.
REDUCTION_MEAN = "mean"
REDUCTION_MODE = "mode"
REDUCTIONS = (REDUCTION_MEAN, REDUCTION_MODE)


def default_reduction(dtype: np.dtype | str, *, is_label: bool) -> str:
    """Mode for labels, mean for images.

    ``is_label`` is the caller's declaration rather than a guess from dtype: a
    uint16 array is equally plausible as EM intensity or as instance ids, and
    guessing wrong silently corrupts one of them.
    """
    return REDUCTION_MODE if is_label else REDUCTION_MEAN


def _pad_to_multiple(block: np.ndarray, factors: tuple[int, int, int]) -> np.ndarray:
    """Pad with edge values so the shape divides evenly by ``factors``.

    Edge padding rather than zeros: zero is *background* in label data, so
    zero-padding would dilute a boundary block toward background and erode the
    edge of every object. Repeating the edge keeps the block's own content.
    """
    pads = []
    for axis in range(3):
        size, factor = block.shape[axis], factors[axis]
        remainder = (-size) % factor
        pads.append((0, remainder))
    if not any(p[1] for p in pads):
        return block
    return np.pad(block, pads, mode="edge")


def _blocked(block: np.ndarray, factors: tuple[int, int, int]) -> np.ndarray:
    """Reshape to ``(oz, fz, oy, fy, ox, fx)`` so blocks are contiguous axes."""
    fz, fy, fx = factors
    oz, oy, ox = (
        block.shape[0] // fz,
        block.shape[1] // fy,
        block.shape[2] // fx,
    )
    return block.reshape(oz, fz, oy, fy, ox, fx)


def reduce_block(
    block: np.ndarray,
    factors: tuple[int, int, int],
    *,
    reduction: str = REDUCTION_MEAN,
) -> np.ndarray:
    """Reduce ``block`` by ``factors``, returning the same dtype.

    The output shape is the ceiling division of the input by the factors, so a
    partial trailing block still produces an output voxel — dropping it would
    silently truncate the volume.
    """
    if reduction not in REDUCTIONS:
        raise ValueError(f"Unknown reduction {reduction!r}.")
    if block.ndim != 3:
        raise ValueError("Reduction operates on 3-D (z, y, x) blocks.")
    if any(f < 1 for f in factors):
        raise ValueError(f"Factors must be >= 1: {factors}.")
    if factors == (1, 1, 1):
        return block.copy()

    dtype = block.dtype
    padded = _pad_to_multiple(block, factors)
    grouped = _blocked(padded, factors)

    if reduction == REDUCTION_MEAN:
        # float64 accumulation, then round back — averaging uint16 in-place
        # would overflow on a bright block.
        out = grouped.mean(axis=(1, 3, 5))
        if np.issubdtype(dtype, np.integer):
            info = np.iinfo(dtype)
            out = np.clip(np.rint(out), info.min, info.max)
        return out.astype(dtype)

    return _mode_reduce(grouped, dtype)


def _mode_reduce(grouped: np.ndarray, dtype: np.dtype) -> np.ndarray:
    """Most common **non-zero** value per block; 0 only if the block is all 0.

    Vectorised over blocks rather than looping: a 512² plane at factor 2 is
    65 536 blocks, and a Python loop over those is the difference between
    milliseconds and seconds per plane.
    """
    oz, fz, oy, fy, ox, fx = grouped.shape
    flat = grouped.transpose(0, 2, 4, 1, 3, 5).reshape(oz * oy * ox, fz * fy * fx)

    out = np.zeros(flat.shape[0], dtype=dtype)
    # Blocks that are entirely background stay background; everything else is
    # decided among its non-zero values only.
    any_fg = (flat != 0).any(axis=1)
    if not any_fg.any():
        return out.reshape(oz, oy, ox)

    candidates = flat[any_fg]
    # Per row, the most frequent non-zero value. Sorting each row groups equal
    # values; run boundaries then give counts without a Python loop.
    ordered = np.sort(candidates, axis=1)
    # A change marks the start of a run; background sorts first and is excluded
    # by scoring it below every real count.
    change = np.ones_like(ordered, dtype=bool)
    change[:, 1:] = ordered[:, 1:] != ordered[:, :-1]

    best = np.zeros(ordered.shape[0], dtype=dtype)
    best_count = np.zeros(ordered.shape[0], dtype=np.int64)
    run_value = np.zeros(ordered.shape[0], dtype=ordered.dtype)
    run_count = np.zeros(ordered.shape[0], dtype=np.int64)

    for col in range(ordered.shape[1]):
        value = ordered[:, col]
        starts = change[:, col]
        # Close the previous run where a new one starts.
        better = starts & (run_count > best_count) & (run_value != 0)
        best[better] = run_value[better]
        best_count[better] = run_count[better]
        run_value = np.where(starts, value, run_value)
        run_count = np.where(starts, 1, run_count + 1)

    better = (run_count > best_count) & (run_value != 0)
    best[better] = run_value[better]

    out[any_fg] = best
    return out.reshape(oz, oy, ox)


def slab_plan(parent_depth: int, factor_z: int) -> list[tuple[int, int]]:
    """Half-open ``(z0, z1)`` slabs of the parent, one per output plane.

    This is what keeps a build bounded: the caller reads only ``factor_z``
    planes at a time, reduces them to one output plane, writes it, and moves on.
    Peak memory is a slab, not a volume.
    """
    if factor_z < 1:
        raise ValueError("factor_z must be >= 1.")
    plan = []
    z = 0
    while z < parent_depth:
        plan.append((z, min(z + factor_z, parent_depth)))
        z += factor_z
    return plan


def reduce_slab(
    slab: np.ndarray,
    factors: tuple[int, int, int],
    *,
    reduction: str = REDUCTION_MEAN,
) -> np.ndarray:
    """Reduce one z-slab to a single output plane, shaped ``(1, y, x)``."""
    if slab.ndim != 3:
        raise ValueError("A slab is 3-D (z, y, x).")
    reduced = reduce_block(slab, factors, reduction=reduction)
    if reduced.shape[0] != 1:
        # A short trailing slab reduces to one plane by construction; anything
        # else means the caller sliced wrongly and would silently write the
        # wrong z.
        raise ValueError(
            f"Slab reduced to {reduced.shape[0]} planes, expected 1 — "
            "the slab passed does not match factor_z."
        )
    return reduced
