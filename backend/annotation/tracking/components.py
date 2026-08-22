"""Provider-independent seed decomposition and cross-layer branch inference.

The Track rail used to make the annotator create one child class per
disconnected blob. That is bookkeeping the machine can do: a single logical
parent whose committed prompt happens to contain several separated regions is
still one mitochondrion, and the regions are simply the branches SAM2 has to
follow independently.

This module owns that inference, and nothing in it knows what a tracking
provider is:

* :func:`split_components` — 8-connected components of one prompt slice, with
  the accidental-speck filter from :mod:`annotation.tracking.config`;
* :func:`infer_branches` — turns ``{manual child -> {z -> mask}}`` into a
  deterministic list of :class:`InferredBranch` objects, associating the
  components of *later* prompt layers with the branch they continue rather
  than pairing them by scan order (which swaps identities as soon as two
  mitochondria cross in y/x).

Determinism matters twice over: rerunning the same prompts must produce the
same branch ordering so audit metadata and temporary provider ids are stable,
and the association must never depend on dictionary iteration order.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import config

#: 8-connectivity, i.e. a full 3x3 structuring element — the same convention as
#: the legacy ``branching.split_binary_mask_components`` this replaces.
_STRUCTURE_8 = np.ones((3, 3), dtype=np.int8)


@dataclass(frozen=True)
class SeedComponent:
    """One 8-connected blob of a committed prompt slice."""

    z: int
    #: 1-based position within its own layer, in raster order of first pixel.
    #: Stable for identical input, which is what makes branch keys stable.
    component_index: int
    mask: np.ndarray
    area: int
    centroid: tuple[float, float]

    def audit(self) -> dict:
        return {
            "z": int(self.z),
            "component_index": int(self.component_index),
            "area": int(self.area),
            "centroid": [round(float(self.centroid[0]), 2), round(float(self.centroid[1]), 2)],
        }


@dataclass
class InferredBranch:
    """One ephemeral propagation branch inferred from the prompt geometry.

    ``branch_key`` is a small 1..N audit id local to a single propagation. It is
    deliberately *not* a label id: the temporary provider object id is allocated
    separately and every branch is merged back into the requested parent label
    at the end.
    """

    branch_key: int
    #: Which manual child class the components came from. ``1`` in the normal
    #: workflow, where the annotator never creates child classes at all.
    subclass_index: int
    seeds: dict[int, np.ndarray] = field(default_factory=dict)
    components: list[SeedComponent] = field(default_factory=list)

    @property
    def seed_zs(self) -> list[int]:
        return sorted(int(z) for z in self.seeds)

    def add(self, component: SeedComponent) -> None:
        existing = self.seeds.get(component.z)
        self.seeds[component.z] = (
            component.mask if existing is None else (existing | component.mask)
        )
        self.components.append(component)

    def audit(self) -> dict:
        return {
            "branch_key": int(self.branch_key),
            "subclass_index": int(self.subclass_index),
            "seed_zs": self.seed_zs,
            "components": [component.audit() for component in self.components],
        }


@dataclass
class BranchInference:
    branches: list[InferredBranch] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    #: Components removed by the speck filter, kept for the preview summary so
    #: a dropped blob is visible rather than silently gone.
    dropped: list[dict] = field(default_factory=list)


def _components_of(mask: np.ndarray, z: int, min_area: int, dropped: list[dict]):
    """8-connected components of ``mask``, speck-filtered, in raster order.

    The filter is deliberately *relative*: a component below ``min_area`` is
    dropped only when a larger one survives on the same layer. A prompt whose
    every component is tiny is taken at face value — the annotator drew a small
    mitochondrion on purpose, and silently deleting it (or worse, leaving the
    parent with no seeds at all and failing the propagation) would be the exact
    "removes legitimate small mitochondria" failure the threshold exists to
    avoid. What it does remove is the 1-3 px crumb left beside a real stroke.
    """
    binary = np.asarray(mask, dtype=bool)
    if not binary.any():
        return []
    try:
        import scipy.ndimage as ndi

        labelled, count = ndi.label(binary, structure=_STRUCTURE_8)
    except ImportError:  # pragma: no cover - scipy is a declared dependency
        labelled, count = _label_8_fallback(binary)
    if count <= 0:
        return []
    areas = np.bincount(labelled.ravel(), minlength=count + 1)

    found: list[tuple[int, int, np.ndarray, tuple[float, float]]] = []
    for cc in range(1, count + 1):
        area = int(areas[cc])
        if area <= 0:
            continue  # empty components are ignored outright
        component = labelled == cc
        ys, xs = np.nonzero(component)
        found.append((cc, area, component, (float(ys.mean()), float(xs.mean()))))
    if not found:
        return []

    keep = [entry for entry in found if entry[1] >= min_area]
    if not keep:
        keep = [max(found, key=lambda entry: (entry[1], -entry[0]))]
    keep_ccs = {entry[0] for entry in keep}
    for cc, area, _component, centroid in found:
        if cc in keep_ccs:
            continue
        dropped.append(
            {
                "z": int(z),
                "area": int(area),
                "centroid": [round(centroid[0], 2), round(centroid[1], 2)],
                "reason": "below_min_component_area",
                "min_component_area": int(min_area),
            }
        )

    out: list[SeedComponent] = []
    for index, (_cc, area, component, centroid) in enumerate(keep, start=1):
        out.append(
            SeedComponent(
                z=int(z),
                component_index=index,
                mask=component,
                area=int(area),
                centroid=centroid,
            )
        )
    return out


def _label_8_fallback(binary: np.ndarray):
    """Union-find 8-connected labelling used only when scipy is absent."""
    height, width = binary.shape
    labels = np.zeros((height, width), dtype=np.int32)
    parent: list[int] = [0]

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for y in range(height):
        row = binary[y]
        for x in np.nonzero(row)[0]:
            neighbours = []
            for ny, nx in ((y - 1, x - 1), (y - 1, x), (y - 1, x + 1), (y, x - 1)):
                if 0 <= ny < height and 0 <= nx < width and labels[ny, nx]:
                    neighbours.append(int(labels[ny, nx]))
            if neighbours:
                smallest = min(neighbours)
                labels[y, x] = smallest
                for other in neighbours:
                    union(smallest, other)
            else:
                parent.append(len(parent))
                labels[y, x] = len(parent) - 1
    remap: dict[int, int] = {}
    for y in range(height):
        for x in np.nonzero(labels[y])[0]:
            root = find(int(labels[y, x]))
            labels[y, x] = remap.setdefault(root, len(remap) + 1)
    return labels, len(remap)


def split_components(
    mask: np.ndarray, *, min_area: int | None = None
) -> list[np.ndarray]:
    """8-connected component masks of ``mask``, largest-to-smallest order aside.

    Components smaller than ``min_area`` (default
    :func:`annotation.tracking.config.min_component_area`) are dropped as
    accidental brush specks. Order is raster order of each component's first
    pixel, which is stable for identical input.
    """
    if min_area is None:
        min_area = config.min_component_area()
    return [
        component.mask
        for component in _components_of(mask, 0, int(min_area), [])
    ]


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    intersection = int(np.count_nonzero(a & b))
    if not intersection:
        return 0.0
    union = int(np.count_nonzero(a | b))
    return intersection / union if union else 0.0


def _equivalent_radius(area: int) -> float:
    return float(np.sqrt(max(1, int(area)) / np.pi))


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


@dataclass
class _Candidate:
    branch: InferredBranch
    component: SeedComponent
    iou: float
    distance: float
    credible: bool

    def sort_key(self):
        # Highest overlap first, then nearest, then stable ids. Every term is
        # deterministic, so identical prompts always associate identically.
        return (-self.iou, self.distance, self.branch.branch_key, self.component.component_index)


def infer_branches(
    branch_seeds: dict[int, dict[int, np.ndarray]],
    *,
    min_area: int | None = None,
    distance_factor: float | None = None,
    ambiguous_margin: float | None = None,
) -> BranchInference:
    """Infer ephemeral propagation branches from committed prompt masks.

    ``branch_seeds`` maps *manual* child index -> ``{z: 2D bool mask}``. In the
    normal workflow there is exactly one manual child and the disconnected
    regions inside it become branches automatically. Manual children remain an
    advanced override: they are never associated with each other, but each one
    is still decomposed, so a manual child holding two disconnected blobs is not
    handed to the provider as one indivisible object.

    Association across prompt layers uses each branch's most recent committed
    location as its predicted position on the next prompted layer, matched by
    maximum IoU with centroid distance as the tie-break. A component with no
    credible match starts a new branch at that layer; no component is ever
    assigned to two branches.
    """
    min_area = config.min_component_area() if min_area is None else int(min_area)
    distance_factor = (
        config.match_distance_factor() if distance_factor is None else float(distance_factor)
    )
    ambiguous_margin = (
        config.ambiguous_iou_margin() if ambiguous_margin is None else float(ambiguous_margin)
    )

    inference = BranchInference()
    next_key = 1

    for subclass_index in sorted(int(k) for k in branch_seeds):
        per_z = branch_seeds[subclass_index] if subclass_index in branch_seeds else branch_seeds[str(subclass_index)]
        subclass_branches: list[InferredBranch] = []
        #: The branch's latest committed geometry — its prediction for the next
        #: prompted layer. Prompts are the only evidence available *before*
        #: propagation, so "predicted location" is "where it last was".
        last_seen: dict[int, SeedComponent] = {}

        for z in sorted(int(z) for z in per_z):
            raw = per_z[z] if z in per_z else per_z[str(z)]
            components = _components_of(raw, z, min_area, inference.dropped)
            if not components:
                continue
            if not subclass_branches:
                for component in components:
                    branch = InferredBranch(
                        branch_key=next_key, subclass_index=int(subclass_index)
                    )
                    next_key += 1
                    branch.add(component)
                    subclass_branches.append(branch)
                    last_seen[branch.branch_key] = component
                continue

            candidates: list[_Candidate] = []
            for branch in subclass_branches:
                previous = last_seen[branch.branch_key]
                for component in components:
                    iou = _iou(previous.mask, component.mask)
                    distance = _distance(previous.centroid, component.centroid)
                    reach = distance_factor * (
                        _equivalent_radius(previous.area)
                        + _equivalent_radius(component.area)
                    )
                    candidates.append(
                        _Candidate(
                            branch=branch,
                            component=component,
                            iou=iou,
                            distance=distance,
                            credible=iou > 0.0 or distance <= reach,
                        )
                    )
            candidates.sort(key=_Candidate.sort_key)

            taken_branches: set[int] = set()
            taken_components: set[int] = set()
            assignments: list[_Candidate] = []
            for candidate in candidates:
                if not candidate.credible:
                    continue
                if candidate.branch.branch_key in taken_branches:
                    continue
                if candidate.component.component_index in taken_components:
                    continue
                taken_branches.add(candidate.branch.branch_key)
                taken_components.add(candidate.component.component_index)
                assignments.append(candidate)

            _collect_ambiguities(
                assignments, candidates, ambiguous_margin, inference.warnings
            )

            for candidate in assignments:
                candidate.branch.add(candidate.component)
                last_seen[candidate.branch.branch_key] = candidate.component

            # Anything credible-but-unmatched, or with no credible match at
            # all, is a new structure appearing at this layer: its own branch.
            for component in components:
                if component.component_index in taken_components:
                    continue
                branch = InferredBranch(
                    branch_key=next_key, subclass_index=int(subclass_index)
                )
                next_key += 1
                branch.add(component)
                subclass_branches.append(branch)
                last_seen[branch.branch_key] = component

        inference.branches.extend(subclass_branches)

    return inference


def _collect_ambiguities(
    assignments: list[_Candidate],
    candidates: list[_Candidate],
    margin: float,
    warnings: list[dict],
) -> None:
    """Report near-ties rather than silently picking one identity.

    The assignment itself is already deterministic; this only tells the Track
    preview that the deterministic answer was close to a coin flip, so the
    annotator can look before confirming. Two shapes of near-tie matter: one
    component that fits several branches equally well, and one branch that
    several components fit equally well.
    """
    for chosen in assignments:
        rival_branches = sorted(
            {
                int(other.branch.branch_key)
                for other in candidates
                if other.credible
                and other.component.component_index == chosen.component.component_index
                and other.branch.branch_key != chosen.branch.branch_key
                and abs(other.iou - chosen.iou) <= margin
            }
        )
        rival_components = sorted(
            {
                int(other.component.component_index)
                for other in candidates
                if other.credible
                and other.branch.branch_key == chosen.branch.branch_key
                and other.component.component_index != chosen.component.component_index
                and abs(other.iou - chosen.iou) <= margin
            }
        )
        if not rival_branches and not rival_components:
            continue
        warnings.append(
            {
                "code": "ambiguous_component_association",
                "message": (
                    f"Seed component {chosen.component.component_index} on layer "
                    f"z={chosen.component.z} matches more than one branch about "
                    "equally well; review the preview before confirming."
                ),
                "z": int(chosen.component.z),
                "component_index": int(chosen.component.component_index),
                "assigned_branch": int(chosen.branch.branch_key),
                "rival_branches": rival_branches,
                "rival_components": rival_components,
                "metrics": {
                    "iou": round(float(chosen.iou), 4),
                    "distance": round(float(chosen.distance), 2),
                },
            }
        )
