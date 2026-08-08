"""Phase 8 — deterministic interpolation core.

Pure functions over numpy arrays. **No Django, no database, no filesystem, no
network, no global state.** That is what makes the golden tests meaningful: the
mathematics can be exercised without standing up an application.

The algorithm, from ``07-webknossos-interpolation-analysis.md`` §"Algorithm
(CODE — verified)":

1. binary masks for the active label on the first and last slice;
2. signed distance transform of each — outside positive, inside negative;
3. for each intermediate at ``k = offset / depth``:
   ``weighted = first*(1-k) + last*k``; label where ``weighted < 0``.

Independently reimplemented from that written description per master prompt §E8
("prefer independent reimplementation of SDF method with citation"). No
WEBKNOSSOS source was consulted, copied, ported or paraphrased; the distance
transform comes from scipy (BSD-3), which is already a declared dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

# Named so the constant is traceable to the specification rather than magic.
MAXIMUM_INTERPOLATION_DEPTH = 100   # doc 07: MAXIMUM_INTERPOLATION_DEPTH
MINIMUM_INTERPOLATION_DEPTH = 2     # doc 07: "depth must be >= 2"

ALGORITHM_NAME = "sdf-linear-blend"
ALGORITHM_VERSION = 1

# Guards the float64 intermediates: an SDF pair costs 8 bytes per voxel each.
DEFAULT_MAX_VOXELS = 64 * 1024 * 1024


class InterpolationError(ValueError):
    """Invalid interpolation input. Carries a machine-readable reason."""

    def __init__(self, message: str, *, reason: str = "invalid"):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class InterpolationPlan:
    """What interpolation *would* do. Computing this writes nothing.

    The preview half of §E8's "Interpolate -> preview -> confirm/cancel": a
    caller can render or discard this without having touched stored labels.
    """

    label_id: int
    depth: int
    spacing: tuple[float, float]
    overwrite_mode: str
    # offset (1..depth-1) -> boolean mask of voxels this label would occupy.
    masks: dict[int, np.ndarray]
    voxels_changed: int
    algorithm: str = ALGORITHM_NAME
    algorithm_version: int = ALGORITHM_VERSION

    @property
    def offsets(self) -> list[int]:
        return sorted(self.masks)


def signed_distance(mask: np.ndarray, spacing=(1.0, 1.0)) -> np.ndarray:
    """Signed distance to the mask boundary: outside positive, inside negative.

    Built as ``edt(~mask) - edt(mask)``, which is the sign-corrected equivalent
    of transforming the mask and its inverse and combining by absolute maximum
    (doc 07 step 6). A voxel just inside the object reads −1 and just outside
    +1, so the ``< 0`` test in :func:`blend` labels exactly the interior.

    ``spacing`` is **physical** distance per voxel along each axis, so
    anisotropy is handled by the metric rather than by a correction applied
    afterwards.
    """
    if mask.dtype != bool:
        mask = mask.astype(bool)
    if not mask.any():
        # An empty mask has no zero level set, so its signed distance is
        # undefined. Refusing beats inventing +inf and silently producing an
        # empty interpolation that looks like a valid answer.
        raise InterpolationError(
            "Cannot compute a signed distance for an empty mask: there is no "
            "surface to measure from.",
            reason="empty_mask",
        )
    if mask.all():
        raise InterpolationError(
            "Cannot compute a signed distance for a fully-filled mask: there "
            "is no surface to measure from.",
            reason="full_mask",
        )
    outside = ndimage.distance_transform_edt(~mask, sampling=spacing)
    inside = ndimage.distance_transform_edt(mask, sampling=spacing)
    return outside - inside


def blend(first_sdf: np.ndarray, last_sdf: np.ndarray, k: float) -> np.ndarray:
    """Linear blend of two signed distance fields at fraction ``k``.

    Labels where the blend is **strictly** negative. Doc 07 says "draw where
    weightedAverage < 0", taken literally: a zero is the surface itself, and
    including it would thicken every interpolated object by one voxel.
    """
    if not 0.0 <= k <= 1.0:
        raise InterpolationError(f"Blend fraction must be in [0,1], got {k}.")
    weighted = first_sdf * (1.0 - k) + last_sdf * k
    return weighted < 0.0


def validate_inputs(first, last, *, depth, label_id, spacing,
                    overwrite_mode, max_voxels=DEFAULT_MAX_VOXELS) -> None:
    """Reject bad input before any expensive allocation.

    Every check here is cheap and happens before the distance transforms, which
    are the expensive part. Nothing is silently coerced: an ambiguous request is
    an error, not a guess.
    """
    if first.ndim != 2 or last.ndim != 2:
        raise InterpolationError(
            f"Endpoint slices must be 2-D, got {first.ndim}-D and {last.ndim}-D.",
            reason="bad_rank",
        )
    if first.shape != last.shape:
        raise InterpolationError(
            f"Endpoint shapes differ: {first.shape} vs {last.shape}.",
            reason="shape_mismatch",
        )
    if int(label_id) == 0:
        raise InterpolationError(
            "Label 0 is background and cannot be interpolated.",
            reason="reserved_label",
        )
    if int(label_id) < 0:
        raise InterpolationError(
            "Label ids must be non-negative.", reason="negative_label"
        )
    if depth < MINIMUM_INTERPOLATION_DEPTH:
        raise InterpolationError(
            f"Interpolation depth must be at least "
            f"{MINIMUM_INTERPOLATION_DEPTH}, got {depth}.",
            reason="depth_too_small",
        )
    if depth > MAXIMUM_INTERPOLATION_DEPTH:
        raise InterpolationError(
            f"Interpolation depth {depth} exceeds the maximum of "
            f"{MAXIMUM_INTERPOLATION_DEPTH}.",
            reason="depth_too_large",
        )
    if len(spacing) != 2:
        raise InterpolationError(
            f"Spacing must have one entry per plane axis, got {spacing}.",
            reason="bad_spacing",
        )
    if not all(np.isfinite(s) and s > 0 for s in spacing):
        raise InterpolationError(
            f"Spacing entries must be finite and positive, got {spacing}.",
            reason="bad_spacing",
        )
    if overwrite_mode not in OVERWRITE_MODES:
        raise InterpolationError(
            f"Unknown overwrite mode {overwrite_mode!r}; "
            f"expected one of {sorted(OVERWRITE_MODES)}.",
            reason="bad_overwrite_mode",
        )
    voxels = int(first.size) * int(depth)
    if voxels > max_voxels:
        raise InterpolationError(
            f"Interpolation would touch {voxels} voxels, over the "
            f"{max_voxels} limit. Interpolate a smaller bounding box.",
            reason="too_large",
        )


# Phase 9 promoted these to shared tool infrastructure: doc 19 ranks overwrite
# policies as a tool-level P1, not an interpolation detail, so leaving them here
# would force every future tool to import from interpolation. Re-exported under
# the original names so nothing that referenced them has to change.
from annotation.tools.overwrite import (  # noqa: E402
    OVERWRITE_ALL,
    OVERWRITE_EMPTY,
    OVERWRITE_MODES,
)


def plan(first_labels, last_labels, *, label_id: int, depth: int,
         spacing=(1.0, 1.0), overwrite_mode: str = OVERWRITE_EMPTY,
         max_voxels: int = DEFAULT_MAX_VOXELS) -> InterpolationPlan:
    """Compute the intermediate masks. **Writes nothing.**

    ``first_labels`` / ``last_labels`` are 2-D label arrays for the endpoint
    slices; only ``label_id`` is interpolated, matching §E8's "active segment
    only".

    Deterministic: the output is a pure function of the inputs, with no
    dependence on iteration order, component ordering, or any tolerance.
    """
    first_labels = np.asarray(first_labels)
    last_labels = np.asarray(last_labels)
    validate_inputs(
        first_labels, last_labels, depth=depth, label_id=label_id,
        spacing=spacing, overwrite_mode=overwrite_mode, max_voxels=max_voxels,
    )

    first_mask = first_labels == label_id
    last_mask = last_labels == label_id
    if not first_mask.any() or not last_mask.any():
        raise InterpolationError(
            f"Label {label_id} must be present on both endpoint slices; "
            f"interpolating from an empty endpoint is extrapolation, not "
            f"interpolation.",
            reason="missing_endpoint_label",
        )

    first_sdf = signed_distance(first_mask, spacing=spacing)
    last_sdf = signed_distance(last_mask, spacing=spacing)

    masks, changed = {}, 0
    for offset in range(1, depth):
        mask = blend(first_sdf, last_sdf, offset / depth)
        masks[offset] = mask
        changed += int(mask.sum())

    return InterpolationPlan(
        label_id=int(label_id), depth=int(depth),
        spacing=(float(spacing[0]), float(spacing[1])),
        overwrite_mode=overwrite_mode, masks=masks, voxels_changed=changed,
    )


def apply_to_slice(labels: np.ndarray, mask: np.ndarray, *, label_id: int,
                   overwrite_mode: str = OVERWRITE_EMPTY) -> np.ndarray:
    """Write ``label_id`` into ``labels`` where ``mask``, honouring the mode.

    Returns a **new** array; the input is never mutated, so a caller holding a
    memmap cannot be corrupted by a computation that later fails.
    """
    if overwrite_mode not in OVERWRITE_MODES:
        raise InterpolationError(
            f"Unknown overwrite mode {overwrite_mode!r}.",
            reason="bad_overwrite_mode",
        )
    if labels.shape != mask.shape:
        raise InterpolationError(
            f"Slice shape {labels.shape} does not match mask {mask.shape}.",
            reason="shape_mismatch",
        )
    if not _fits_dtype(label_id, labels.dtype):
        raise InterpolationError(
            f"Label {label_id} is not representable in {labels.dtype}.",
            reason="label_dtype_overflow",
        )

    out = labels.copy()
    if overwrite_mode == OVERWRITE_EMPTY:
        # Existing segments win. Destroying somebody's work silently is the
        # worse failure, so this is the default.
        mask = mask & (labels == 0)
    out[mask] = label_id
    return out


def _fits_dtype(value: int, dtype) -> bool:
    info = np.iinfo(dtype) if np.issubdtype(dtype, np.integer) else None
    if info is None:
        return True
    return info.min <= value <= info.max
