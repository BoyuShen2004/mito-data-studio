"""Child touch/merge lifecycle for propagated branches (provider-independent).

SAM2 has no idea that two of the objects it is faithfully following are two
lobes of the same mitochondrion, so it happily keeps both alive after they run
into each other and the union of their masks starts double-counting the same
biology. Deciding when two inferred children have *merged* — and which one
stops — is a post-processing question about the returned masks, not a question
about the model, so it lives here and applies identically to every provider.

Canonical direction is the user's explicit ``start_z -> end_z``. SAM2 may still
infer bidirectionally for prediction quality (see ``adapters/sam2.py``); that is
an inference detail and deliberately does not define merge ordering, because
otherwise the same prompts would produce different lineage depending on which
seed happened to be first.

Rules, all thresholds from :mod:`annotation.tracking.config`:

* contact = overlap **or** direct 8-connected adjacency;
* a single noisy edge pixel is not contact — a run is confirmed only when it is
  either strong on one layer or sustained across consecutive layers;
* a confirmed run is **backdated** to its first layer, so the merge is recorded
  where the children actually met rather than where the evidence crossed the
  threshold;
* on the merge layer both children still contribute to the parent mask; the
  loser is discarded only on *later* layers;
* survivor choice is deterministic and never kills a child the annotator has
  prompted after the contact layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import config

_STRUCTURE_8 = np.ones((3, 3), dtype=np.int8)


def dilate8(mask: np.ndarray) -> np.ndarray:
    """One-pixel 8-connected dilation (includes the original mask)."""
    binary = np.asarray(mask, dtype=bool)
    if not binary.any():
        return binary
    try:
        import scipy.ndimage as ndi

        return ndi.binary_dilation(binary, structure=_STRUCTURE_8)
    except ImportError:  # pragma: no cover - scipy is a declared dependency
        out = binary.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                out |= np.roll(np.roll(binary, dy, axis=0), dx, axis=1)
        return out


def mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """``(y0, y1, x0, x1)`` half-open bounds of the True pixels, or ``None``.

    Microscopy planes here are tens of megapixels while one mitochondrion
    occupies a few thousand of them, so every pairwise test below is done on
    the overlap of two bounding boxes rather than on the full plane.
    """
    binary = np.asarray(mask, dtype=bool)
    rows = np.flatnonzero(binary.any(axis=1))
    if not rows.size:
        return None
    cols = np.flatnonzero(binary.any(axis=0))
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def contact_strength(
    a: np.ndarray,
    b: np.ndarray,
    *,
    bbox_a: tuple[int, int, int, int] | None = None,
    bbox_b: tuple[int, int, int, int] | None = None,
) -> int:
    """Pixels of ``b`` that touch ``a``, counting overlap and 8-adjacency alike.

    One number for both kinds of contact keeps the thresholds honest: a merge
    that shows up as a two-pixel overlap and one that shows up as a two-pixel
    shared border are equally (un)convincing, and should be treated the same.

    Pass precomputed bounding boxes when they are already known; the work is
    then bounded by the region the two masks actually share.
    """
    left = np.asarray(a, dtype=bool)
    right = np.asarray(b, dtype=bool)
    box_a = mask_bbox(left) if bbox_a is None else bbox_a
    box_b = mask_bbox(right) if bbox_b is None else bbox_b
    if box_a is None or box_b is None:
        return 0
    # Grow ``a``'s box by the one pixel the dilation can reach, then intersect.
    y0 = max(box_a[0] - 1, box_b[0])
    y1 = min(box_a[1] + 1, box_b[1])
    x0 = max(box_a[2] - 1, box_b[2])
    x1 = min(box_a[3] + 1, box_b[3])
    if y0 >= y1 or x0 >= x1:
        return 0
    # Dilating the *cropped* left mask would lose pixels just outside the crop
    # that reach into it, so crop one pixel wider and trim afterwards.
    cy0, cy1 = max(0, y0 - 1), min(left.shape[0], y1 + 1)
    cx0, cx1 = max(0, x0 - 1), min(left.shape[1], x1 + 1)
    grown = dilate8(left[cy0:cy1, cx0:cx1])
    grown = grown[y0 - cy0 : y1 - cy0, x0 - cx0 : x1 - cx0]
    return int(np.count_nonzero(grown & right[y0:y1, x0:x1]))


@dataclass
class MergeEvent:
    """One confirmed child-into-child merge, as recorded for audit/lineage."""

    loser_branch: int
    survivor_branch: int
    contact_z: int
    reason: str
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "loser_branch": int(self.loser_branch),
            "survivor_branch": int(self.survivor_branch),
            "contact_z": int(self.contact_z),
            "reason": str(self.reason),
            "metrics": dict(self.metrics),
        }


@dataclass
class ContactResolution:
    """Outcome of the lifecycle pass over one parent's propagated branches."""

    #: ``branch_key -> last layer the branch contributes to``. Absent means the
    #: branch runs to ``end_z``.
    terminated_at: dict[int, int] = field(default_factory=dict)
    events: list[MergeEvent] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def audit(self) -> dict:
        return {
            "merge_events": [event.to_dict() for event in self.events],
            "terminated_at": {str(k): int(v) for k, v in sorted(self.terminated_at.items())},
        }


@dataclass
class _Run:
    """A maximal run of consecutive layers on which one pair is in contact."""

    start_z: int
    length: int = 0
    peak: int = 0


def _median_recent_area(
    masks: dict[int, np.ndarray], contact_z: int, start_z: int, window: int
) -> float:
    """Rolling median area over the layers just before (and at) ``contact_z``.

    A branch's area on the contact layer alone is exactly the noisiest possible
    measurement — the two children are overlapping there — so the decision is
    taken over a short window of already-processed layers instead.
    """
    layers = [z for z in range(contact_z - window, contact_z) if z >= start_z]
    layers = [z for z in layers if z in masks]
    if not layers:
        layers = [contact_z] if contact_z in masks else []
    if not layers:
        return 0.0
    areas = [float(np.count_nonzero(masks[z])) for z in layers]
    return float(np.median(areas))


def resolve_branch_contacts(
    masks: dict[int, dict[int, np.ndarray]],
    *,
    start_z: int,
    end_z: int,
    seed_zs: dict[int, list[int]] | None = None,
) -> ContactResolution:
    """Terminate merged children and return the lineage events.

    ``masks`` maps ``branch_key -> {z: 2D bool mask}`` and is **mutated in
    place**: a losing branch's masks are deleted on layers after the merge, and
    nowhere else. ``seed_zs`` maps ``branch_key -> committed prompt layers``;
    it is what makes rule 1 (never kill a child the annotator prompted later)
    possible.

    Layers are visited in canonical ``start_z -> end_z`` order, and each
    confirmed merge removes its loser from the active set immediately, so a
    three-way pile-up resolves into a chain of independent pairwise events with
    no cycles and no contradictory pairs.
    """
    start_z, end_z = int(start_z), int(end_z)
    if end_z < start_z:
        raise ValueError(f"Invalid canonical range ({start_z}, {end_z})")
    seed_zs = {int(k): sorted(int(z) for z in v) for k, v in (seed_zs or {}).items()}

    strong = config.strong_contact_pixels()
    minimum = config.min_contact_pixels()
    sustain = config.contact_sustain_layers()
    window = config.area_window_layers()

    resolution = ContactResolution()
    active = sorted(int(key) for key in masks)
    runs: dict[tuple[int, int], _Run] = {}
    #: Pairs already reported as ambiguous — warn once, not once per layer.
    ambiguous: set[tuple[int, int]] = set()

    for z in range(start_z, end_z + 1):
        boxes: dict[int, tuple[int, int, int, int]] = {}
        for key in active:
            plane = masks.get(key, {}).get(z)
            if plane is None:
                continue
            box = mask_bbox(plane)
            if box is not None:
                boxes[key] = box
        present = [key for key in active if key in boxes]
        seen_pairs: set[tuple[int, int]] = set()
        for position, first in enumerate(present):
            for second in present[position + 1 :]:
                if first not in active or second not in active:
                    continue  # already merged away earlier in this same layer
                pair = (first, second)
                seen_pairs.add(pair)
                strength = contact_strength(
                    masks[first][z],
                    masks[second][z],
                    bbox_a=boxes[first],
                    bbox_b=boxes[second],
                )
                run = runs.get(pair)
                if strength <= 0:
                    runs.pop(pair, None)
                    continue
                if run is None or run.start_z + run.length != z:
                    run = _Run(start_z=z)
                    runs[pair] = run
                run.length += 1
                run.peak = max(run.peak, strength)
                confirmed = run.peak >= strong or (
                    run.length >= sustain and run.peak >= minimum
                )
                if not confirmed or pair in ambiguous:
                    continue
                # Backdate to where the two children actually met.
                contact_z = run.start_z
                decision = _choose_survivor(
                    first,
                    second,
                    masks=masks,
                    contact_z=contact_z,
                    start_z=start_z,
                    seed_zs=seed_zs,
                    window=window,
                )
                metrics = {
                    "contact_strength": int(run.peak),
                    "contact_layers": int(run.length),
                    "confirmed_z": int(z),
                    **decision["metrics"],
                }
                if decision["survivor"] is None:
                    ambiguous.add(pair)
                    resolution.warnings.append(
                        {
                            "code": "ambiguous_child_merge",
                            "message": (
                                f"Inferred children {first} and {second} merge at layer "
                                f"z={contact_z} but both have prompts after it; both were "
                                "kept for review instead of terminating either."
                            ),
                            "branches": [int(first), int(second)],
                            "contact_z": int(contact_z),
                            "metrics": metrics,
                        }
                    )
                    continue
                survivor = decision["survivor"]
                loser = decision["loser"]
                resolution.events.append(
                    MergeEvent(
                        loser_branch=loser,
                        survivor_branch=survivor,
                        contact_z=contact_z,
                        reason=decision["reason"],
                        metrics=metrics,
                    )
                )
                resolution.terminated_at[loser] = contact_z
                # Both children contribute on the contact layer itself; only
                # later layers are discarded.
                for later in [zz for zz in masks[loser] if zz > contact_z]:
                    del masks[loser][later]
                active = [key for key in active if key != loser]
                runs = {
                    key: value for key, value in runs.items() if loser not in key
                }
        # A pair that lost contact on this layer restarts its run next time.
        for pair in list(runs):
            if pair not in seen_pairs:
                runs.pop(pair, None)

    return resolution


def _choose_survivor(
    first: int,
    second: int,
    *,
    masks: dict[int, dict[int, np.ndarray]],
    contact_z: int,
    start_z: int,
    seed_zs: dict[int, list[int]],
    window: int,
) -> dict:
    """Deterministic survivor rules; ``survivor=None`` means "ambiguous"."""
    later_first = [z for z in seed_zs.get(first, []) if z > contact_z]
    later_second = [z for z in seed_zs.get(second, []) if z > contact_z]
    metrics = {
        "later_prompts": {str(first): later_first, str(second): later_second},
    }

    # 1. A child the annotator prompted past the contact layer is evidence the
    #    annotator expects it to continue. It must not lose to one without.
    if later_first and not later_second:
        return {"survivor": first, "loser": second, "reason": "later_prompt", "metrics": metrics}
    if later_second and not later_first:
        return {"survivor": second, "loser": first, "reason": "later_prompt", "metrics": metrics}

    # 2. Both prompted later: the user asserted two identities past the merge.
    #    Silently killing either would throw away work, so neither dies.
    if later_first and later_second:
        return {"survivor": None, "loser": None, "reason": "ambiguous_later_prompts", "metrics": metrics}

    # 3. Neither prompted later: the smaller branch normally ends, measured
    #    over a window so one noisy layer cannot decide it.
    area_first = _median_recent_area(masks[first], contact_z, start_z, window)
    area_second = _median_recent_area(masks[second], contact_z, start_z, window)
    metrics["median_area"] = {str(first): area_first, str(second): area_second}
    if area_first != area_second:
        survivor, loser = (
            (first, second) if area_first > area_second else (second, first)
        )
        return {"survivor": survivor, "loser": loser, "reason": "smaller_branch", "metrics": metrics}

    # 4. Genuinely identical: the stable branch id is the last word, so a rerun
    #    of the same prompts produces the same lineage.
    survivor, loser = (first, second) if first < second else (second, first)
    return {"survivor": survivor, "loser": loser, "reason": "stable_branch_id", "metrics": metrics}
