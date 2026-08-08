"""Fork-aware instance-label bookkeeping for SAM2 tracking.

Ports the multi-branch idea from ``MTS/mts_mask_editor`` to mito-data-agent's
service layer: when a mitochondrion **forks**, each 8-connected branch is seeded
as its own temporary track id so SAM2 can follow the branches independently, but
they all belong to one logical group. After propagation the whole group is
**auto-merged** back into a single final mitochondria instance, so a fork never
leaves two permanently-separate mitochondria unless the user explicitly splits
them.

Everything here is pure NumPy so it is fast, GPU-free, and unit-testable; the
GPU SAM2 work lives behind the tracking provider (see ``adapters/``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


# --- Connected components (ported from MTS core.mask_utils) -----------------

def _neighbors8(y: int, x: int, height: int, width: int):
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            ny, nx = y + dy, x + dx
            if 0 <= ny < height and 0 <= nx < width:
                yield ny, nx


def split_binary_mask_components(mask: np.ndarray) -> list[np.ndarray]:
    """Split a boolean mask into its 8-connected component masks.

    Mirrors ``MTS`` so fork branches are seeded identically: each disconnected
    blob in a seed slice becomes one branch.
    """
    m = np.asarray(mask, dtype=bool)
    if not m.any():
        return []
    h, w = m.shape
    visited = np.zeros((h, w), dtype=bool)
    components: list[np.ndarray] = []
    for y in range(h):
        for x in range(w):
            if not m[y, x] or visited[y, x]:
                continue
            comp = np.zeros((h, w), dtype=bool)
            stack = [(y, x)]
            visited[y, x] = True
            comp[y, x] = True
            while stack:
                cy, cx = stack.pop()
                for ny, nx in _neighbors8(cy, cx, h, w):
                    if m[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        comp[ny, nx] = True
                        stack.append((ny, nx))
            components.append(comp)
    return components


def next_free_id(volume_mask: np.ndarray, reserved=None) -> int:
    """Smallest positive label absent from ``volume_mask`` and ``reserved``."""
    reserved = {int(i) for i in (reserved or []) if int(i) > 0}
    used = (
        {int(i) for i in np.unique(volume_mask) if int(i) > 0}
        if volume_mask.size
        else set()
    )
    used |= reserved
    iid = 1
    while iid in used:
        iid += 1
    return iid


# --- Track groups -----------------------------------------------------------

@dataclass
class TrackGroup:
    """One logical mitochondrion tracked as several temporary branches.

    * ``group_id``   — stable id for the logical mito (also the final instance id
                       once merged, by convention).
    * ``branch_ids`` — the temporary per-branch track labels used *during*
                       propagation (one per fork branch / connected component).
    * ``final_id``   — the single instance id the group is merged into after
                       tracking (defaults to ``group_id``).
    * ``seed_z``     — z index the branches were seeded from (audit / re-run).
    """

    group_id: int
    branch_ids: list[int] = field(default_factory=list)
    final_id: int | None = None
    seed_z: int | None = None
    # Explicit user subclasses are local to this group.  The keys are the
    # small 1..N indices shown in Track; the values are the temporary provider
    # object ids used only while propagating.  Older audit rows omit this.
    subclass_branch_ids: dict[int, int] = field(default_factory=dict)
    seed_zs: list[int] = field(default_factory=list)

    def resolved_final_id(self) -> int:
        return self.final_id if self.final_id is not None else self.group_id

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "branch_ids": list(self.branch_ids),
            "final_id": self.resolved_final_id(),
            "seed_z": self.seed_z,
            "subclass_branch_ids": {
                str(k): int(v) for k, v in self.subclass_branch_ids.items()
            },
            "seed_zs": list(self.seed_zs),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrackGroup":
        return cls(
            group_id=int(data["group_id"]),
            branch_ids=[int(b) for b in data.get("branch_ids", [])],
            final_id=(None if data.get("final_id") is None else int(data["final_id"])),
            seed_z=(None if data.get("seed_z") is None else int(data["seed_z"])),
            subclass_branch_ids={
                int(k): int(v)
                for k, v in data.get("subclass_branch_ids", {}).items()
            },
            seed_zs=[int(z) for z in data.get("seed_zs", [])],
        )


def merge_group(volume_mask: np.ndarray, group: TrackGroup) -> np.ndarray:
    """Collapse every branch label of ``group`` into its single final id.

    This is the auto-merge run after tracking: all temporary branch tracks that
    came from one fork become one mitochondria instance. Idempotent.
    """
    final_id = group.resolved_final_id()
    for bid in group.branch_ids:
        if bid == final_id:
            continue
        volume_mask[volume_mask == bid] = final_id
    return volume_mask
