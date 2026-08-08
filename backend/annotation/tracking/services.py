"""Fork-aware SAM2 tracking orchestration (provider-agnostic).

``run_branch_tracking`` is the one place that turns a user's seed mask(s) for a
single logical mitochondrion into a propagated instance, handling forks the way
the spec requires:

1. each 8-connected branch of the seed is given its **own temporary track id**;
2. all branches are kept under one :class:`~annotation.tracking.branching.TrackGroup`;
3. the provider (CPU ``local`` or GPU ``sam2``) propagates each branch;
4. the whole group is **auto-merged into one final instance id** afterwards.

Pure-ish: it mutates the passed ``volume_mask`` array in place and returns a
metadata dict (temporary branch ids, final id, group membership) to persist for
audit / undo / re-run. No Django models are touched here.
"""

from __future__ import annotations

import numpy as np

from .branching import (
    TrackGroup,
    merge_group,
    next_free_id,
    split_binary_mask_components,
)
from .interfaces import PropagationRequest
from .registry import get_tracking_provider


def run_branch_tracking(
    *,
    image: np.ndarray,
    volume_mask: np.ndarray,
    seeds: dict[int, np.ndarray],
    z_range: tuple[int, int] | None = None,
    provider=None,
    group_id: int | None = None,
    reserved=None,
    branch_seeds: dict[int, dict[int, np.ndarray]] | None = None,
    protect_other_labels: bool = True,
    branch_id_floor: int = 1,
) -> dict:
    """Propagate one (possibly forked) mitochondrion and merge its branches.

    ``seeds`` maps ``z -> 2D bool mask`` and retains the legacy automatic
    connected-component split.  ``branch_seeds`` is the preferred explicit
    form: ``local subclass index -> {z: mask}``.  Local indices never become
    volume labels; fresh temporary provider ids are allocated for them.
    """
    if image.ndim != 3:
        raise ValueError("image must be a 3D (Z, Y, X) array")
    z_max = image.shape[0] - 1
    z_range = z_range or (0, z_max)
    z_range = (int(z_range[0]), int(z_range[1]))
    if z_range[0] < 0 or z_range[1] > z_max or z_range[1] < z_range[0]:
        raise ValueError(f"Invalid z_range {z_range} for {image.shape[0]} slices")
    provider = provider or get_tracking_provider()
    reserved = {int(i) for i in (reserved or []) if int(i) > 0}

    if group_id is None:
        group_id = next_free_id(volume_mask, reserved)
    reserved.add(group_id)

    # 1. One temporary track id per fork branch. The first branch reuses the
    #    final id; all others use ids absent from the working volume. Explicit
    #    local subclass indices are deliberately not used as label ids.
    provider_seeds: dict[int, dict[int, np.ndarray]] = {}
    branch_ids: list[int] = []
    subclass_branch_ids: dict[int, int] = {}
    next_branch_id = max(1, int(branch_id_floor))

    def allocate_branch_id() -> int:
        nonlocal next_branch_id
        while next_branch_id in reserved or next_branch_id in branch_ids:
            next_branch_id += 1
        answer = next_branch_id
        next_branch_id += 1
        return answer

    def add_branch(local_index: int, per_z: dict[int, np.ndarray]):
        clean = {}
        for z, mask in per_z.items():
            z = int(z)
            mask = np.asarray(mask, dtype=bool)
            if not z_range[0] <= z <= z_range[1]:
                raise ValueError(f"Seed z={z} is outside tracking range {z_range}")
            if mask.shape != image.shape[1:]:
                raise ValueError(
                    f"Seed mask shape {mask.shape} does not match image plane {image.shape[1:]}"
                )
            if mask.any():
                clean[z] = mask
        if not clean:
            return
        if not branch_ids:
            bid = group_id
        else:
            # The plan service may pass a cropped z slab. Its maximum is not a
            # safe whole-volume allocator, so temporary branch ids start above
            # the global max supplied by the caller.
            bid = allocate_branch_id()
        branch_ids.append(bid)
        subclass_branch_ids[int(local_index)] = bid
        provider_seeds[bid] = clean

    if branch_seeds is not None:
        for local_index, per_z in sorted(branch_seeds.items()):
            add_branch(int(local_index), per_z)
    else:
        local_index = 1
        for z, sl in sorted(seeds.items()):
            for comp in split_binary_mask_components(sl):
                add_branch(local_index, {int(z): comp})
                local_index += 1

    if not branch_ids:
        return {"final_id": group_id, "branch_ids": [], "group": None}

    group = TrackGroup(
        group_id=group_id,
        branch_ids=branch_ids,
        seed_z=min(z for per_z in provider_seeds.values() for z in per_z),
        subclass_branch_ids=subclass_branch_ids,
        seed_zs=sorted({z for per_z in provider_seeds.values() for z in per_z}),
    )

    # 2. Propagate every branch across the z-range (GPU on a real provider).
    result = provider.propagate(
        PropagationRequest(image=image, seeds=provider_seeds, z_range=z_range)
    )

    # 3. Write each branch's propagated mask with its temporary id.
    for bid, per_z in result.masks.items():
        for z, m in per_z.items():
            destination = volume_mask[int(z)]
            write = np.asarray(m, dtype=bool)
            if protect_other_labels:
                # Sequential groups in one batch may overlap. Earlier parents
                # and unrelated brush/AI labels win; this group may only fill
                # background or refresh its own final-label voxels.
                write &= (destination == 0) | (destination == group_id)
            destination[write] = bid

    # 4. Auto-merge the whole fork group into one final mitochondria instance.
    merge_group(volume_mask, group)

    return {
        "final_id": group.resolved_final_id(),
        "branch_ids": branch_ids,
        "group": group.to_dict(),
    }
