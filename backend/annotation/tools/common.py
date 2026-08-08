"""Shared conventions and validation for every Phase 9 tool.

Fixed once here so tools cannot drift apart on the things that are easy to get
subtly inconsistent: axis order, box conventions, label semantics, and the size
caps that keep a small HTTP request from triggering a large scan.

Conventions
-----------
* axis order ``(z, y, x)``; a 2-D plane is ``(row, col) == (y, x)``
* bounding box ``(z0, y0, x0, z1, y1, x1)``, **half-open** on the upper bound,
  matching numpy slicing so no translation layer exists to get wrong
* label ``0`` is background and reserved, as in Phase 8
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: Payload schema for tool operations. Bump when a payload's meaning changes;
#: readers refuse versions they do not understand rather than guessing.
TOOL_SCHEMA_VERSION = 1

DEFAULT_MAX_VOXELS = 16 * 1024 * 1024
DEFAULT_MAX_FILL_DEPTH = 32
MAX_PLANE_DIMENSION = 8192


class ToolError(ValueError):
    """A tool request that cannot be satisfied. Carries a reason code."""

    def __init__(self, message: str, *, reason: str = "invalid"):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class BoundingBox:
    """Half-open ``(z0, y0, x0, z1, y1, x1)`` in voxel indices."""

    z0: int
    y0: int
    x0: int
    z1: int
    y1: int
    x1: int

    @property
    def shape(self) -> tuple[int, int, int]:
        return (self.z1 - self.z0, self.y1 - self.y0, self.x1 - self.x0)

    @property
    def voxels(self) -> int:
        d, h, w = self.shape
        return d * h * w

    def as_list(self) -> list[int]:
        return [self.z0, self.y0, self.x0, self.z1, self.y1, self.x1]

    @classmethod
    def from_sequence(cls, seq) -> "BoundingBox":
        if len(seq) != 6:
            raise ToolError(
                f"A bounding box needs 6 values (z0,y0,x0,z1,y1,x1), got "
                f"{len(seq)}.", reason="bad_bbox",
            )
        return cls(*(int(v) for v in seq))

    def validate(self, *, max_voxels: int = DEFAULT_MAX_VOXELS) -> None:
        if any(v < 0 for v in self.as_list()):
            raise ToolError("Bounding box coordinates must be non-negative.",
                            reason="negative_coordinate")
        d, h, w = self.shape
        if d <= 0 or h <= 0 or w <= 0:
            raise ToolError(
                f"Bounding box must be non-empty on every axis, got shape "
                f"{self.shape}. Upper bounds are exclusive.",
                reason="empty_bbox",
            )
        if h > MAX_PLANE_DIMENSION or w > MAX_PLANE_DIMENSION:
            raise ToolError(
                f"Plane dimensions {h}x{w} exceed the {MAX_PLANE_DIMENSION} "
                f"limit.", reason="plane_too_large",
            )
        if self.voxels > max_voxels:
            raise ToolError(
                f"Bounding box covers {self.voxels} voxels, over the "
                f"{max_voxels} limit. Use a smaller region.",
                reason="too_large",
            )


@dataclass(frozen=True)
class ToolPlan:
    """What a tool *would* change. Computing this mutates nothing."""

    tool: str
    tool_schema_version: int
    #: offset within the box -> boolean mask of voxels that would be written
    masks: dict[int, np.ndarray]
    voxels_changed: int
    bbox: BoundingBox
    label_id: int
    overwrite_mode: str
    warnings: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    @property
    def offsets(self) -> list[int]:
        return sorted(self.masks)


def validate_label(label_id, *, dtype=None) -> int:
    """Labels are positive and representable. 0 is background and reserved."""
    try:
        value = int(label_id)
    except (TypeError, ValueError):
        raise ToolError(f"Label id must be an integer, got {label_id!r}.",
                        reason="bad_label")
    if value == 0:
        raise ToolError("Label 0 is background and cannot be written by a tool.",
                        reason="reserved_label")
    if value < 0:
        raise ToolError("Label ids must be non-negative.",
                        reason="negative_label")
    if dtype is not None and np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        if not (info.min <= value <= info.max):
            raise ToolError(
                f"Label {value} is not representable in {dtype}.",
                reason="label_dtype_overflow",
            )
    return value


def validate_volume_block(block: np.ndarray) -> None:
    """A tool operates on a dense 3-D block in (z, y, x) order."""
    if block.ndim != 3:
        raise ToolError(
            f"Tools operate on 3-D (z,y,x) blocks, got {block.ndim}-D. "
            f"Pass a single-slice block with depth 1 for a 2-D tool.",
            reason="bad_rank",
        )
    if not np.issubdtype(block.dtype, np.integer):
        raise ToolError(
            f"Label blocks must have an integer dtype, got {block.dtype}.",
            reason="bad_dtype",
        )
