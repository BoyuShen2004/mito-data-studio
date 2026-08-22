"""Fork-aware instance-label bookkeeping for SAM2 tracking.

Ports the multi-branch idea from ``MTS/mts_mask_editor`` to mito-data-studio's
service layer: when a mitochondrion **forks**, each 8-connected branch is seeded
as its own temporary track id so SAM2 can follow the branches independently, but
they all belong to one logical group. After propagation the whole group is
**auto-merged** back into a single final mitochondria instance, so a fork never
leaves two permanently-separate mitochondria unless the user explicitly splits
them.

This module is the group bookkeeping half of that idea. Its two neighbours own
the rest, and are equally provider-independent: :mod:`annotation.tracking.
components` decides *what the branches are* (component splitting and cross-layer
association), and :mod:`annotation.tracking.contact` decides *when two of them
have merged*. :class:`TrackGroup` carries the resulting audit trail.

Everything here is pure NumPy so it is fast, GPU-free, and unit-testable; the
GPU SAM2 work lives behind the tracking provider (see ``adapters/``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


# --- Connected components ---------------------------------------------------
# The implementation moved to :mod:`annotation.tracking.components`, which adds
# the accidental-speck filter and the cross-layer association the automatic
# branch inference needs. This wrapper keeps the original unfiltered contract
# for the legacy ``run_branch_tracking(seeds=...)`` path and its callers.


def split_binary_mask_components(mask: np.ndarray) -> list[np.ndarray]:
    """Split a boolean mask into its 8-connected component masks.

    Unfiltered: every component is returned, however small. Callers that want
    accidental brush specks removed should use
    :func:`annotation.tracking.components.split_components`, whose default
    ``min_area`` comes from :mod:`annotation.tracking.config`.
    """
    from .components import split_components

    return split_components(mask, min_area=1)


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
    # --- Explicit propagation bounds (inclusive) ---------------------------
    # The range the *user* chose, not a value derived from the seed layers.
    # Carried on the group so the audit row explains what was actually asked
    # for, independently of where the seeds happened to land.
    start_z: int | None = None
    end_z: int | None = None
    # --- Automatic branch inference / merge lifecycle ----------------------
    # ``inferred_branches`` explains which seed components became which
    # ephemeral branch; ``branch_provider_ids`` maps those audit keys to the
    # temporary provider object ids; ``merge_events`` and ``terminated_at``
    # record the child touch/merge lifecycle; ``warnings`` carries structured
    # ambiguities for the Track preview. All are audit-only: none of them ever
    # becomes a permanent label id.
    inferred_branches: list[dict] = field(default_factory=list)
    branch_provider_ids: dict[int, int] = field(default_factory=dict)
    merge_events: list[dict] = field(default_factory=list)
    terminated_at: dict[int, int] = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)
    dropped_components: list[dict] = field(default_factory=list)

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
            "start_z": (None if self.start_z is None else int(self.start_z)),
            "end_z": (None if self.end_z is None else int(self.end_z)),
            "inferred_branches": [dict(b) for b in self.inferred_branches],
            "branch_provider_ids": {
                str(k): int(v) for k, v in self.branch_provider_ids.items()
            },
            "merge_events": [dict(e) for e in self.merge_events],
            "terminated_at": {str(k): int(v) for k, v in self.terminated_at.items()},
            "warnings": [dict(w) for w in self.warnings],
            "dropped_components": [dict(d) for d in self.dropped_components],
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
            start_z=(None if data.get("start_z") is None else int(data["start_z"])),
            end_z=(None if data.get("end_z") is None else int(data["end_z"])),
            inferred_branches=[dict(b) for b in data.get("inferred_branches", [])],
            branch_provider_ids={
                int(k): int(v) for k, v in data.get("branch_provider_ids", {}).items()
            },
            merge_events=[dict(e) for e in data.get("merge_events", [])],
            terminated_at={
                int(k): int(v) for k, v in data.get("terminated_at", {}).items()
            },
            warnings=[dict(w) for w in data.get("warnings", [])],
            dropped_components=[dict(d) for d in data.get("dropped_components", [])],
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
