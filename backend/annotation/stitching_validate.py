"""Stitching phase 7a: validate the stitched objects.

Three checks, all on the work directory written by phases 1-6:

- **consistency** (no tile reads): every object's voxel total equals the sum of
  its members in ``id_map.csv``; every multi-label object is connected only
  through "yes" decisions in ``decisions_resolved.csv``; no object or label
  appears twice. Any violation refuses the run.
- **repeated-tile objects**: an object holding two or more labels from one tile
  is either a proofreading split (the labels touch inside that tile) or a path
  that leaves the tile and comes back, or a wrong "yes" (they do not). Only the
  union of those labels' boxes (``label_boxes.csv`` from phase 6) is read.
- **unusual objects**: the largest objects by member count and by cable length,
  for visual checking.

The design is in ``docs/engineering/stitching.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from annotation.stitching import (
    REGION_VOXELS,
    StitchingError,
    UnionFind,
    Z_BLOCK_BYTES,
    _listing,
    build_inventory,
    load_tile_ids,
    load_tiles_csv,
    open_tile,
    read_csv,
    require_stitchable,
    write_csv,
)
from annotation.stitching_measure import BBOX_FIELDS, LABEL_BOXES_FIELDS, box_union

RESOLVED_VALUES = ("yes", "no", "unsure", "undecided")
REPEATED_FIELDS = ("object_id", "tile", "labels", "global_ids", "classification", *BBOX_FIELDS)
LARGEST_FIELDS = ("ranked_by", "rank", "object_id", "member_count", "tiles", "length_um", "volume_um3", *BBOX_FIELDS)
TOUCHING, SEPARATE = "touching", "separate"
DEFAULT_TOP = 20


# --- consistency -------------------------------------------------------------

@dataclass
class WorkData:
    """The phase 2-6 tables, loaded once and checked by ``check_consistency``."""
    id_rows: list[dict]
    object_rows: list[dict]            # objects.csv as read
    objects: dict[int, int]            # global id -> object id
    groups: dict[int, list[int]]       # object id -> member global ids, ascending
    summary: list[dict]                # objects_summary.csv
    resolved: list[dict]               # decisions_resolved.csv
    measurements: list[dict]           # measurements.csv
    measurement_fields: list[str] = field(default_factory=list)


def _duplicates(values) -> list:
    seen, dupes = set(), []
    for v in values:
        if v in seen and v not in dupes:
            dupes.append(v)
        seen.add(v)
    return dupes


def load_work(work) -> WorkData:
    work = Path(work)
    id_rows = read_csv(work / "id_map.csv")
    object_rows = read_csv(work / "objects.csv")
    measurements = read_csv(work / "measurements.csv")
    fields = list(measurements[0]) if measurements else []
    objects = {int(r["global_id"]): int(r["object_id"]) for r in object_rows}
    groups: dict[int, list[int]] = {}
    for g in sorted(objects):
        groups.setdefault(objects[g], []).append(g)
    return WorkData(id_rows, object_rows, objects, groups, read_csv(work / "objects_summary.csv"),
                    read_csv(work / "decisions_resolved.csv"), measurements, fields)


def check_consistency(work) -> WorkData:
    """Load the work directory and refuse, listing every violation, unless:

    - no label, object or measurement row appears twice, and ``objects.csv``
      covers exactly the labels in ``id_map.csv``;
    - each object's voxel (and region voxel) total in ``objects_summary.csv``
      and ``measurements.csv`` equals the sum over its members in ``id_map.csv``,
      and the grand totals agree;
    - each object's members are connected through "yes" decisions alone, and no
      "yes" joins two objects.
    """
    data = load_work(work)
    problems: list[str] = []

    def report(title, lines):
        if lines:
            problems.append(_listing(title, lines))

    member = {}
    for r in data.id_rows:
        member[int(r["global_id"])] = (r["tile"], int(r["voxel_count"]),
                                       int(r[REGION_VOXELS]) if REGION_VOXELS in r else None)
    report("global ids listed twice in id_map.csv",
           [str(g) for g in _duplicates(int(r["global_id"]) for r in data.id_rows)])
    report("labels listed twice in id_map.csv",
           [f"{t}:{lid}" for t, lid in _duplicates((r["tile"], int(r["local_id"])) for r in data.id_rows)])
    report("global ids listed twice in objects.csv",
           [str(g) for g in _duplicates(int(r["global_id"]) for r in data.object_rows)])
    report("objects listed twice in objects_summary.csv",
           [str(o) for o in _duplicates(int(r["object_id"]) for r in data.summary)])
    report("objects listed twice in measurements.csv",
           [str(o) for o in _duplicates(int(r["object_id"]) for r in data.measurements)])
    missing = sorted(set(member) - set(data.objects))
    extra = sorted(set(data.objects) - set(member))
    report("labels in id_map.csv but not in objects.csv", [str(g) for g in missing])
    report("labels in objects.csv but not in id_map.csv", [str(g) for g in extra])
    report("objects whose object_id is not their smallest member",
           [str(o) for o, members in data.groups.items() if min(members) != o])

    def totals(object_id):
        members = [g for g in data.groups.get(object_id, ()) if g in member]
        region = None if not members or member[members[0]][2] is None else sum(member[g][2] for g in members)
        return len(data.groups.get(object_id, ())), sum(member[g][1] for g in members), region

    wrong = []
    summary_ids = set()
    for row in data.summary:
        o = int(row["object_id"])
        summary_ids.add(o)
        if o not in data.groups:
            wrong.append(f"objects_summary.csv: object {o} is not in objects.csv")
            continue
        count, voxels, _ = totals(o)
        if (int(row["member_count"]), int(row["total_voxel_count"])) != (count, voxels):
            wrong.append(f"objects_summary.csv: object {o} has {row['member_count']} member(s), "
                         f"{row['total_voxel_count']} voxels; id_map.csv gives {count}, {voxels}")
    for o in sorted(set(data.groups) - summary_ids):
        wrong.append(f"objects_summary.csv: object {o} from objects.csv is missing")
    for row in data.measurements:
        o = int(row["object_id"])
        if o not in data.groups:
            wrong.append(f"measurements.csv: object {o} is not in objects.csv")
            continue
        count, voxels, region = totals(o)
        if (int(row["member_count"]), int(row["voxel_count"])) != (count, voxels):
            wrong.append(f"measurements.csv: object {o} has {row['member_count']} member(s), "
                         f"{row['voxel_count']} voxels; id_map.csv gives {count}, {voxels}")
        if REGION_VOXELS in row and region is not None and int(row[REGION_VOXELS]) != region:
            wrong.append(f"measurements.csv: object {o} has {row[REGION_VOXELS]} region voxels; "
                         f"id_map.csv gives {region}")
    summary_total = sum(int(r["total_voxel_count"]) for r in data.summary)
    id_total = sum(v for _, v, _ in member.values())
    if summary_total != id_total:
        wrong.append(f"objects_summary.csv holds {summary_total} voxels in all; id_map.csv holds {id_total}")
    report("voxel totals that do not match id_map.csv", wrong)

    bad_values = [f"decisions_resolved.csv:{i}: decision {r['decision']!r}"
                  for i, r in enumerate(data.resolved, 2) if r["decision"] not in RESOLVED_VALUES]
    report("invalid values in decisions_resolved.csv", bad_values)
    uf = UnionFind(sorted(set(member) | set(data.objects)))
    crossing = []
    for i, r in enumerate(data.resolved, 2):
        if r["decision"] != "yes":
            continue
        ga, gb = int(r["global_a"]), int(r["global_b"])
        if ga not in uf.parent or gb not in uf.parent:
            crossing.append(f"decisions_resolved.csv:{i}: global id {ga if ga not in uf.parent else gb} is unknown")
            continue
        uf.union(ga, gb)
        if data.objects.get(ga) != data.objects.get(gb):
            crossing.append(f"decisions_resolved.csv:{i}: yes for {ga} -> {gb} joins objects "
                            f"{data.objects.get(ga)} and {data.objects.get(gb)}")
    report("yes decisions that join two objects", crossing)
    split = []
    for o, members in data.groups.items():
        parts = {uf.find(g) for g in members}
        if len(parts) > 1:
            split.append(f"object {o}: {len(members)} labels fall into {len(parts)} groups by yes decisions")
    report("objects not connected through yes decisions alone", split)

    if problems:
        raise StitchingError("consistency check failed\n" + "\n".join(problems))
    return data


# --- repeated-tile objects ---------------------------------------------------

def load_label_boxes(work) -> dict[int, tuple[int, ...]]:
    path = Path(work) / "label_boxes.csv"
    if not path.exists():
        raise StitchingError(f"{path} not found; rerun stitch_measure (phase 6)")
    return {int(r["global_id"]): tuple(int(r[k]) for k in LABEL_BOXES_FIELDS[3:]) for r in read_csv(path)}


def _add_contacts(a: np.ndarray, b: np.ndarray, labels: np.ndarray, pairs: set) -> None:
    """Record every pair of distinct ``labels`` that sit at the same position
    in ``a`` and ``b`` (two arrays of face neighbours)."""
    hit = (a != b) & np.isin(a, labels) & np.isin(b, labels)
    if hit.any():
        for x, y in np.unique(np.stack([a[hit], b[hit]], axis=1), axis=0).tolist():
            pairs.add((min(x, y), max(x, y)))


def label_contacts(volume, crop, labels, *, block_bytes: int = Z_BLOCK_BYTES) -> set[tuple[int, int]]:
    """Pairs of ``labels`` that share a face (6-connectivity) inside ``crop``
    (local ``(z0, z1, y0, y1, x0, x1)``) of ``volume``. Reads ``crop`` in Z
    blocks of about ``block_bytes``, carrying the last plane of each block."""
    labels = np.asarray(sorted(labels))
    plane = (crop[3] - crop[2]) * (crop[5] - crop[4]) * np.dtype(volume.dtype).itemsize
    step = max(1, block_bytes // max(1, plane))
    pairs: set[tuple[int, int]] = set()
    previous = None
    for z in range(crop[0], crop[1], step):
        block = np.asarray(volume[z:min(z + step, crop[1]), crop[2]:crop[3], crop[4]:crop[5]])
        if previous is not None:
            _add_contacts(previous, block[0], labels, pairs)
        for axis in range(3):
            lo = tuple(slice(None, -1) if a == axis else slice(None) for a in range(3))
            hi = tuple(slice(1, None) if a == axis else slice(None) for a in range(3))
            _add_contacts(block[lo], block[hi], labels, pairs)
        previous = block[-1]
    return pairs


def _connected(labels, pairs) -> bool:
    uf = UnionFind(labels)
    for a, b in pairs:
        uf.union(a, b)
    return len({uf.find(lid) for lid in labels}) == 1


def repeated_tile_rows(work, data: WorkData, *, log=lambda message: None) -> list[dict]:
    """One row per (object, tile) where the object holds 2+ labels of that tile."""
    work = Path(work)
    inventory = build_inventory(load_tiles_csv(work))
    require_stitchable(inventory)
    load_tile_ids(work, inventory.tiles)   # refuses tiles changed since phase 2
    tiles = {t.name: t for t in inventory.tiles}
    order = {t.name: i for i, t in enumerate(inventory.tiles)}
    member = {int(r["global_id"]): (r["tile"], int(r["local_id"])) for r in data.id_rows}
    jobs: dict[str, list[tuple[int, list[int]]]] = {}
    for o, members in data.groups.items():
        by_tile: dict[str, list[int]] = {}
        for g in members:
            by_tile.setdefault(member[g][0], []).append(g)
        for name, gs in by_tile.items():
            if len(gs) > 1:
                jobs.setdefault(name, []).append((o, gs))
    if not jobs:
        return []
    boxes = load_label_boxes(work)
    rows = []
    for name in sorted(jobs, key=order.__getitem__):
        tile = tiles[name]
        with open_tile(tile.path) as volume:
            for o, gs in jobs[name]:
                if any(g not in boxes for g in gs):
                    raise StitchingError(f"label_boxes.csv lacks labels of object {o}; rerun stitch_measure")
                box = box_union(boxes[g] for g in gs)
                crop = tuple(v - tile.lo(a // 2) for a, v in enumerate(box))
                local_ids = [member[g][1] for g in gs]
                touching = _connected(local_ids, label_contacts(volume, crop, local_ids))
                rows.append({"object_id": o, "tile": name, "labels": ";".join(map(str, local_ids)),
                             "global_ids": ";".join(map(str, gs)),
                             "classification": TOUCHING if touching else SEPARATE,
                             **dict(zip(BBOX_FIELDS, box))})
        log(f"{name}: {len(jobs[name])} repeated-tile object(s) checked")
    return sorted(rows, key=lambda r: (r["object_id"], order[r["tile"]]))


# --- unusual objects ---------------------------------------------------------

def largest_rows(measurements: list[dict], top: int = DEFAULT_TOP) -> list[dict]:
    """The ``top`` objects by member count and the ``top`` by cable length
    (measured lengths only); ties go to the smaller object id."""
    def row(rank_by, rank, r):
        return {"ranked_by": rank_by, "rank": rank, "object_id": r["object_id"], "member_count": r["member_count"],
                "tiles": r["tiles"], "length_um": r["skeleton_length_um"], "volume_um3": r["volume_um3"],
                **{f: r.get(f, "") for f in BBOX_FIELDS}}

    by_members = sorted(measurements, key=lambda r: (-int(r["member_count"]), int(r["object_id"])))[:top]
    measured = [r for r in measurements if r["skeleton_length_um"]]
    by_length = sorted(measured, key=lambda r: (-float(r["skeleton_length_um"]), int(r["object_id"])))[:top]
    return ([row("member_count", i, r) for i, r in enumerate(by_members, 1)]
            + [row("length", i, r) for i, r in enumerate(by_length, 1)])


# --- entry point -------------------------------------------------------------

@dataclass
class ValidateResult:
    repeated: list[dict]
    largest: list[dict]
    counts: dict[str, int]


def run_validate(work, *, top: int = DEFAULT_TOP, log=lambda message: None) -> ValidateResult:
    work = Path(work)
    if top < 1:
        raise StitchingError("--top must be positive")
    data = check_consistency(work)
    if any(f not in data.measurement_fields for f in BBOX_FIELDS) and data.measurements:
        raise StitchingError("measurements.csv has no bounding boxes; rerun stitch_measure (phase 6)")
    repeated = repeated_tile_rows(work, data, log=log)
    largest = largest_rows(data.measurements, top)
    write_csv(work / "validate_repeated_tile.csv", REPEATED_FIELDS, repeated)
    write_csv(work / "validate_largest.csv", LARGEST_FIELDS, largest)
    counts = {
        "objects": len(data.groups), "measured_objects": len(data.measurements),
        "repeated_objects": len({r["object_id"] for r in repeated}), "repeated_rows": len(repeated),
        "touching": sum(r["classification"] == TOUCHING for r in repeated),
        "separate": sum(r["classification"] == SEPARATE for r in repeated),
    }
    return ValidateResult(repeated, largest, counts)
