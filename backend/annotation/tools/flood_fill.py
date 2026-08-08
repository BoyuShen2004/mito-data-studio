"""Flood fill, 2-D and limited 3-D (Phase 9, P1).

Doc 19: *"Flood fill 2D (+ limited 3D) — WK fill tool — Classical, not only
SAM."* The point of ranking it P1 is that an annotator should not have to invoke
a neural network to fill a region whose boundary is already drawn.

Pure numpy. No Django, no database, no filesystem — same discipline as Phase 8's
interpolation core, so the geometry can be exercised by golden tests without
standing up an application.

Connectivity is **4 in 2-D and 6 in 3-D**, deliberately. 8- and 26-connectivity
leak diagonally through single-voxel gaps, which is the classic complaint about
fill tools: one stray diagonal and the fill escapes the region the user drew.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from .common import (
    DEFAULT_MAX_FILL_DEPTH,
    DEFAULT_MAX_VOXELS,
    TOOL_SCHEMA_VERSION,
    BoundingBox,
    ToolError,
    ToolPlan,
    validate_label,
    validate_volume_block,
)
from .overwrite import DEFAULT_OVERWRITE_MODE, is_valid_mode, writable_mask

TOOL_NAME = "flood_fill"

# 6-connectivity in (z, y, x); a depth-1 block simply has no z-neighbour in
# range, so the same structure gives 4-connectivity in-plane for free.
_STRUCTURE = ndimage.generate_binary_structure(3, 1)


def fill_mask(block: np.ndarray, seed: tuple[int, int, int]) -> np.ndarray:
    """Voxels connected to ``seed`` sharing its label. Pure and deterministic.

    Implemented with a vectorised connected-component labelling rather than a
    Python breadth-first search. The two are mathematically identical — both
    return exactly the connected component of same-label voxels containing the
    seed — but the Python loop costs one interpreter iteration per voxel, which
    measured **694 ms at 256x256 and 4.9 s at 512x512**. That is unusable for a
    tool a user clicks, so the loop was replaced.

    ``generate_binary_structure(3, 1)`` is the 6-neighbour cross in 3-D; on a
    depth-1 block the z-neighbours fall outside the array, so the same structure
    yields 4-connectivity in-plane. 8- and 26-connectivity are deliberately not
    used: they leak diagonally through single-voxel gaps, which is the classic
    complaint about fill tools, and ``diagonal_no_leak`` in the golden set pins
    that choice.

    Deterministic: connected components are a property of the array, not of
    traversal order, so repeated runs are byte-identical.
    """
    validate_volume_block(block)
    z, y, x = seed
    d, h, w = block.shape
    if not (0 <= z < d and 0 <= y < h and 0 <= x < w):
        raise ToolError(
            f"Seed {seed} is outside the block shape {block.shape}.",
            reason="seed_out_of_bounds",
        )

    same_label = block == block[z, y, x]
    components, _ = ndimage.label(same_label, structure=_STRUCTURE)
    return components == components[z, y, x]


def plan(block: np.ndarray, *, seed: tuple[int, int, int], label_id: int,
         bbox: BoundingBox, overwrite_mode: str = DEFAULT_OVERWRITE_MODE,
         max_voxels: int = DEFAULT_MAX_VOXELS,
         max_depth: int = DEFAULT_MAX_FILL_DEPTH) -> ToolPlan:
    """Compute what a fill would write. **Mutates nothing.**

    ``block`` is the dense (z, y, x) label sub-volume for ``bbox``; the caller
    loads it, so this core never touches storage and the region is bounded by
    construction.
    """
    validate_volume_block(block)
    if not is_valid_mode(overwrite_mode):
        raise ToolError(f"Unknown overwrite mode {overwrite_mode!r}.",
                        reason="bad_overwrite_mode")
    label_id = validate_label(label_id, dtype=block.dtype)
    bbox.validate(max_voxels=max_voxels)

    if block.shape != bbox.shape:
        raise ToolError(
            f"Block shape {block.shape} does not match bounding box "
            f"{bbox.shape}.", reason="shape_mismatch",
        )
    # "Limited 3D": an explicit depth ceiling on top of the voxel cap, so a
    # 3-D fill cannot quietly become a whole-volume flood.
    if bbox.shape[0] > max_depth:
        raise ToolError(
            f"3-D fill depth {bbox.shape[0]} exceeds the {max_depth}-slice "
            f"limit. Fill a shallower region.",
            reason="fill_depth_exceeded",
        )

    warnings: list[str] = []
    connected = fill_mask(block, seed)
    seed_label = int(block[seed])

    if seed_label == label_id:
        # Not an error: clicking a region that already carries the target label
        # is a reasonable thing for a user to do, and answering "zero voxels"
        # is more useful than raising.
        warnings.append(
            f"Seed already carries label {label_id}; nothing to change."
        )

    writable = writable_mask(block, connected, overwrite_mode=overwrite_mode)
    if writable.sum() < connected.sum():
        warnings.append(
            f"{int(connected.sum() - writable.sum())} voxel(s) were skipped "
            f"because they already carry another label "
            f"(overwrite_mode={overwrite_mode})."
        )

    masks = {int(i): writable[i] for i in range(writable.shape[0])}
    return ToolPlan(
        tool=TOOL_NAME,
        tool_schema_version=TOOL_SCHEMA_VERSION,
        masks=masks,
        voxels_changed=int(writable.sum()),
        bbox=bbox,
        label_id=label_id,
        overwrite_mode=overwrite_mode,
        warnings=warnings,
        params={
            "seed": [int(v) for v in seed],
            "seed_label": seed_label,
            "connectivity": 6 if block.shape[0] > 1 else 4,
        },
    )


def apply_to_block(block: np.ndarray, plan: ToolPlan) -> np.ndarray:
    """Return a new block with the plan written. The input is never mutated."""
    out = block.copy()
    for offset, mask in plan.masks.items():
        out[offset][mask] = plan.label_id
    return out
