"""Fork-aware SAM2 tracking orchestration (provider-agnostic).

``run_branch_tracking`` is the one place that turns a user's committed prompt
mask(s) for a single logical mitochondrion into a propagated instance. The
provider only ever runs model inference; everything that decides *identity*
lives here and in the pure modules beside it:

1. the parent's committed prompts are decomposed into 8-connected components,
   speck-filtered, and associated across prompt layers into ephemeral
   **inferred branches** (:mod:`annotation.tracking.components`) — the user no
   longer has to create a child class per disconnected blob;
2. each inferred branch gets its **own temporary provider object id**, so the
   provider follows the branches independently;
3. all branches stay under one :class:`~annotation.tracking.branching.TrackGroup`;
4. after propagation the branches are walked in canonical ``start_z -> end_z``
   order and merged children are terminated
   (:mod:`annotation.tracking.contact`);
5. every surviving branch is **auto-merged into one final instance id**, so a
   propagation never leaves a temporary branch id in the volume.

Pure-ish: it mutates the passed ``volume_mask`` array in place and returns a
metadata dict (inferred branches, lineage events, warnings, final id) to persist
for audit / undo / re-run. No Django models are touched here.

**The z range is explicit and inclusive.** ``z_range=(start_z, end_z)`` is the
range the *user* chose; it is validated, never silently replaced by the seed
minimum/maximum, and every committed seed layer must fall inside it.
"""

from __future__ import annotations

import numpy as np

from .branching import TrackGroup, merge_group, next_free_id
from .components import infer_branches
from .contact import resolve_branch_contacts
from .interfaces import PropagationRequest
from .registry import get_tracking_provider


def validate_z_range(z_range, volume_z_size: int) -> tuple[int, int]:
    """Validate an explicit inclusive ``(start_z, end_z)`` propagation range.

    Raises ``ValueError`` with a message meant for the annotator. This is the
    authoritative check: the frontend disables Propagate for the same reasons,
    but nothing relies on the frontend having done so.
    """
    if z_range is None:
        raise ValueError(
            "Start and End layers are required before propagating."
        )
    try:
        start_z, end_z = (int(z_range[0]), int(z_range[1]))
    except (TypeError, ValueError, IndexError, KeyError) as exc:
        raise ValueError("Start and End layers must be whole numbers.") from exc
    if start_z < 0:
        raise ValueError(f"Start layer {start_z} is below the first layer (0).")
    if end_z >= int(volume_z_size):
        raise ValueError(
            f"End layer {end_z} is past the last layer ({int(volume_z_size) - 1})."
        )
    if end_z < start_z:
        raise ValueError(
            f"End layer {end_z} must not be before Start layer {start_z}; "
            "the range is inclusive."
        )
    return start_z, end_z


def assert_seeds_within_range(
    branch_seeds: dict[int, dict[int, np.ndarray]], start_z: int, end_z: int
) -> None:
    """Every committed seed layer must be inside the explicit inclusive range."""
    outside = sorted(
        {
            int(z)
            for per_z in branch_seeds.values()
            for z, mask in per_z.items()
            if np.any(np.asarray(mask, dtype=bool))
            and not start_z <= int(z) <= end_z
        }
    )
    if outside:
        listed = ", ".join(str(z) for z in outside)
        raise ValueError(
            f"Seed layer(s) {listed} fall outside the selected range "
            f"{start_z}–{end_z} (inclusive). Widen the range or clear those seeds."
        )


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
    """Propagate one parent's prompts and merge every inferred branch into it.

    ``branch_seeds`` maps *manual* child index -> ``{z: 2D bool mask}`` and is
    the form the Track queue uses. ``seeds`` (``{z: mask}``) remains supported
    for the legacy single-prompt endpoint and is treated as one manual child.
    Either way, disconnected regions are split into inferred branches
    automatically; manual children only control which prompts may be associated
    with each other. Manual indices and inferred branch keys never become volume
    labels — fresh temporary provider ids are allocated for them and collapsed
    into ``group_id`` at the end.
    """
    if image.ndim != 3:
        raise ValueError("image must be a 3D (Z, Y, X) array")
    z_size = int(image.shape[0])
    # Legacy callers omit the range entirely; the whole volume is then the
    # explicit range. Anything supplied is validated authoritatively.
    start_z, end_z = validate_z_range(
        z_range if z_range is not None else (0, z_size - 1), z_size
    )
    provider = provider or get_tracking_provider()
    reserved = {int(i) for i in (reserved or []) if int(i) > 0}

    if group_id is None:
        group_id = next_free_id(volume_mask, reserved)
    reserved.add(group_id)

    manual: dict[int, dict[int, np.ndarray]] = {}
    if branch_seeds is not None:
        for local_index, per_z in branch_seeds.items():
            manual[int(local_index)] = {int(z): mask for z, mask in per_z.items()}
    else:
        manual[1] = {int(z): mask for z, mask in (seeds or {}).items()}

    plane_shape = tuple(int(v) for v in image.shape[1:])
    for per_z in manual.values():
        for z, mask in per_z.items():
            if np.asarray(mask).shape != plane_shape:
                raise ValueError(
                    f"Seed mask shape {np.asarray(mask).shape} does not match "
                    f"image plane {plane_shape}"
                )
    assert_seeds_within_range(manual, start_z, end_z)

    # 1. Infer the ephemeral branches from the prompt geometry. Deterministic:
    #    identical prompts always yield identical branch keys and ordering.
    inference = infer_branches(manual)
    if not inference.branches:
        return {
            "final_id": group_id,
            "branch_ids": [],
            "group": None,
            "warnings": list(inference.warnings),
        }

    # 2. One temporary provider object id per inferred branch. The first reuses
    #    the final id; the rest come from ids absent from the working volume.
    #    The plan service may pass a cropped z slab, whose maximum is not a safe
    #    whole-volume allocator, so temporary ids start above the caller's
    #    global max (``branch_id_floor``).
    provider_seeds: dict[int, dict[int, np.ndarray]] = {}
    branch_ids: list[int] = []
    subclass_branch_ids: dict[int, int] = {}
    branch_provider_ids: dict[int, int] = {}
    provider_branch_keys: dict[int, int] = {}
    next_branch_id = max(1, int(branch_id_floor))

    def allocate_branch_id() -> int:
        nonlocal next_branch_id
        while next_branch_id in reserved or next_branch_id in branch_ids:
            next_branch_id += 1
        answer = next_branch_id
        next_branch_id += 1
        return answer

    for branch in inference.branches:
        bid = group_id if not branch_ids else allocate_branch_id()
        branch_ids.append(bid)
        branch_provider_ids[branch.branch_key] = bid
        provider_branch_keys[bid] = branch.branch_key
        provider_seeds[bid] = dict(branch.seeds)
        subclass_branch_ids.setdefault(int(branch.subclass_index), bid)

    group = TrackGroup(
        group_id=group_id,
        branch_ids=branch_ids,
        seed_z=min(z for per_z in provider_seeds.values() for z in per_z),
        subclass_branch_ids=subclass_branch_ids,
        seed_zs=sorted({z for per_z in provider_seeds.values() for z in per_z}),
        start_z=start_z,
        end_z=end_z,
        inferred_branches=[branch.audit() for branch in inference.branches],
        branch_provider_ids=branch_provider_ids,
        warnings=list(inference.warnings),
        dropped_components=list(inference.dropped),
    )

    # 3. Propagate every branch across the explicit range (GPU on a real
    #    provider). SAM2 still runs bidirectionally for prediction quality;
    #    that is inference, and deliberately does not define merge ordering.
    result = provider.propagate(
        PropagationRequest(image=image, seeds=provider_seeds, z_range=(start_z, end_z))
    )

    # 4. Child touch/merge lifecycle, in canonical start_z -> end_z order and
    #    keyed by the stable branch keys rather than by provider ids.
    by_branch: dict[int, dict[int, np.ndarray]] = {}
    for bid, per_z in result.masks.items():
        key = provider_branch_keys.get(int(bid))
        if key is None:
            continue  # a provider echoing an id we never seeded: ignore it
        by_branch[key] = {
            int(z): np.asarray(mask, dtype=bool)
            for z, mask in per_z.items()
            if start_z <= int(z) <= end_z
        }
    resolution = resolve_branch_contacts(
        by_branch,
        start_z=start_z,
        end_z=end_z,
        seed_zs={
            branch.branch_key: branch.seed_zs for branch in inference.branches
        },
    )
    group.merge_events = [event.to_dict() for event in resolution.events]
    group.terminated_at = dict(resolution.terminated_at)
    group.warnings = list(group.warnings) + list(resolution.warnings)

    # 5. Write each surviving branch plane with its temporary id.
    for key, per_z in by_branch.items():
        bid = branch_provider_ids[key]
        for z, m in per_z.items():
            destination = volume_mask[int(z)]
            write = np.asarray(m, dtype=bool)
            if protect_other_labels:
                # Sequential groups in one batch may overlap. Earlier parents
                # and unrelated brush/AI labels win; this group may only fill
                # background or refresh its own final-label voxels.
                write &= (destination == 0) | (destination == group_id)
            destination[write] = bid

    # 6. Auto-merge the whole group into one final mitochondria instance, so
    #    no temporary branch id ever survives into the label volume.
    merge_group(volume_mask, group)

    return {
        "final_id": group.resolved_final_id(),
        "branch_ids": branch_ids,
        "group": group.to_dict(),
        "warnings": list(group.warnings),
    }
