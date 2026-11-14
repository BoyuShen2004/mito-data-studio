"""Stitching phase 6: measure stitched objects.

For every object from phase 5 this computes volume (from ``id_map.csv``
voxel counts, no reading), TEASAR cable length with the per-volume measurement
code, and whether the object touches the edge of the tiled volume.

Cable length reuses ``annotation.measurements`` unchanged: the same label scan
(``label_summary``'s ``_scan_stats``) gives each label's bounding box, the crop
is that box padded by ``CROP_PAD``, and ``skeleton_length_nm`` skeletonises the
crop's mask with the same TEASAR parameters, dust threshold and voxel size.
Crops are built in region coordinates and clipped to the region's bounding box,
so every object gets exactly the mask it would have in one untiled volume:

- single-tile objects are measured during one pass over their tile;
- multi-tile objects are assembled from each member's crop in its tile, then
  skeletonised once as a whole.

The design is in ``docs/engineering/stitching.md``.
"""

from __future__ import annotations

import json
import math
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from annotation.cellable_port.labels_3d import _derive_summary, _scan_stats
from annotation.measurements import (
    CROP_PAD,
    SKIPPED_CROP_LIMIT,
    MeasurementError,
    validate_voxel_size,
)
from annotation.morphology import EMPTY_ROW, MORPHOLOGY_FIELDS, Morphology, measure_morphology
from annotation.stitching import (
    REGION_VOXELS,
    StitchingError,
    Tile,
    build_inventory,
    check_seams_csv,
    code_revision,
    load_region_voxels,
    load_tile_ids,
    load_tiles_csv,
    open_tile,
    parse_tile_name,
    read_csv,
    require_stitchable,
    write_csv,
)

DEFAULT_MAX_CROP_VOXELS = 512_000_000
FACES = ("-z", "+z", "-y", "+y", "-x", "+x")  # index = 2 * axis + (1 for the upper face)
BBOX_FIELDS = ("bbox_z0", "bbox_z1", "bbox_y0", "bbox_y1", "bbox_x0", "bbox_x1")
CENTER_FIELDS = ("center_z_nm", "center_y_nm", "center_x_nm")
MEASUREMENTS_FIELDS = ("object_id", "member_count", "tiles", "voxel_count", "volume_um3", "skeleton_length_um",
                       "touches_edge", "edge_faces", "skipped_reason", "unresolved_contacts", "unproofread_tiles",
                       *BBOX_FIELDS, *CENTER_FIELDS, *MORPHOLOGY_FIELDS)
# Every label's bounding box from the pass-1 scan, so phase 7 can crop without a full read.
LABEL_BOXES_FIELDS = ("global_id", "tile", "local_id", "z0", "z1", "y0", "y1", "x0", "x1")
# Parameters of the last run, for phase 7's report.
MEASURE_RUN = "measure_run.json"
REGION_FIELDS = (REGION_VOXELS, "frac_in_region", "crosses_region_boundary")
UNRESOLVED = ("unsure", "undecided")
# Candidates counted in unresolved_contacts; stitch_review --only reads the key columns.
# No "decision" column, so importing the file into a review page decides nothing.
UNRESOLVED_FIELDS = ("seam", "tile_a", "label_a", "tile_b", "label_b", "global_a", "global_b",
                     "object_a", "object_b", "current_decision", "contact_voxels", "frac_a", "frac_b")


@dataclass
class StitchedObject:
    object_id: int
    members: list[int]                       # global ids, ascending
    tiles: list[str]                         # member tiles in tile order
    voxel_count: int
    skeleton_length_um: float | None = None
    skipped_reason: str = ""
    edge_faces: set = field(default_factory=set)   # {(tile order, face index, tile name)}
    unresolved_contacts: int = 0
    unproofread_tiles: int = 0
    region_voxels: int | None = None                # None without region data
    box: tuple[int, ...] = ()                       # union of the members' boxes, region coordinates
    coord_sums: tuple[int, int, int] = (0, 0, 0)    # sum of z, y, x over the object's voxels
    morphology: Morphology | None = None            # None when skipped (crop_limit)

    def measure(self, mask: np.ndarray, voxel_size) -> None:
        """Cable length and morphology from the object's assembled mask."""
        self.morphology = measure_morphology(mask, voxel_size)
        self.skeleton_length_um = self.morphology.length_nm / 1000.0

    def row(self, voxel_size) -> dict:
        voxel_nm3 = voxel_size[0] * voxel_size[1] * voxel_size[2]
        region = {} if self.region_voxels is None else {
            REGION_VOXELS: self.region_voxels,
            "frac_in_region": repr(self.region_voxels / self.voxel_count),
            "crosses_region_boundary": "true" if 0 < self.region_voxels < self.voxel_count else "false",
        }
        return {
            "object_id": self.object_id, "member_count": len(self.members), "tiles": ";".join(self.tiles),
            "voxel_count": self.voxel_count, "volume_um3": repr(self.voxel_count * voxel_nm3 / 1e9),
            "skeleton_length_um": "" if self.skeleton_length_um is None else repr(self.skeleton_length_um),
            "touches_edge": "true" if self.edge_faces else "false",
            "edge_faces": ";".join(f"{name}:{FACES[face]}" for _, face, name in sorted(self.edge_faces)),
            "skipped_reason": self.skipped_reason, "unresolved_contacts": self.unresolved_contacts,
            "unproofread_tiles": self.unproofread_tiles,
            **dict(zip(BBOX_FIELDS, self.box)),
            # Centroid: mean voxel index times the voxel size (voxel i is at i * size nm).
            **{f: repr(self.coord_sums[a] / self.voxel_count * voxel_size[a]) for a, f in enumerate(CENTER_FIELDS)},
            **(self.morphology.row() if self.morphology is not None else EMPTY_ROW),
            **region,
        }


# --- geometry ----------------------------------------------------------------

def region_box(tiles: list[Tile]) -> tuple[int, ...]:
    return tuple(v for a in range(3) for v in (min(t.lo(a) for t in tiles), max(t.hi(a) for t in tiles)))


def padded_window(box, region) -> tuple[int, ...]:
    """``box`` padded by ``CROP_PAD`` and clipped to ``region`` (per-volume crop rule)."""
    return tuple(v for a in range(3)
                 for v in (max(region[2 * a], box[2 * a] - CROP_PAD), min(region[2 * a + 1], box[2 * a + 1] + CROP_PAD)))


def box_union(boxes) -> tuple[int, ...]:
    boxes = list(boxes)
    return tuple(v for a in range(3)
                 for v in (min(b[2 * a] for b in boxes), max(b[2 * a + 1] for b in boxes)))


def box_voxels(box) -> int:
    return math.prod(box[2 * a + 1] - box[2 * a] for a in range(3))


def mask_moments(mask: np.ndarray, window) -> tuple[int, tuple[int, int, int]]:
    """``(voxel count, (sum z, sum y, sum x))`` of ``mask`` placed at
    ``window`` (region coordinates). Exact integers, from per-axis projections."""
    sums = []
    for a in range(3):
        per_plane = np.count_nonzero(mask, axis=tuple(b for b in range(3) if b != a)).astype(np.int64)
        sums.append(int(per_plane @ np.arange(window[2 * a], window[2 * a + 1], dtype=np.int64)))
    return int(np.count_nonzero(mask)), tuple(sums)


def window_moments(window, parts, max_voxels: int) -> tuple[int, tuple[int, int, int]]:
    """``mask_moments`` of a window too large to hold at once, assembled in Z
    slabs of at most ``max_voxels`` (at least one plane)."""
    plane = box_voxels((0, 1, *window[2:]))
    step = max(1, max_voxels // max(1, plane))
    count, sums = 0, [0, 0, 0]
    for z in range(window[0], window[1], step):
        slab = (z, min(z + step, window[1]), *window[2:])
        c, s = mask_moments(assemble_mask(slab, parts), slab)
        count += c
        sums = [a + b for a, b in zip(sums, s)]
    return count, tuple(sums)


def _set_moments(obj: StitchedObject, moments) -> None:
    count, sums = moments
    if count != obj.voxel_count:
        raise StitchingError(f"object {obj.object_id}: {count} voxels in its crop but {obj.voxel_count} in "
                             "id_map.csv; rerun stitch_ids")
    obj.coord_sums = sums


def face_coverage(tile: Tile, seams) -> list[np.ndarray]:
    """For each face of ``tile`` (order ``FACES``), which positions are covered
    by a seam to a neighbouring tile, in the tile's local coordinates of the
    other two axes."""
    cover = []
    for face in range(6):
        axis, upper = divmod(face, 2)
        others = [a for a in range(3) if a != axis]
        mask = np.zeros([tile.shape[a] for a in others], bool)
        for seam in seams:
            if seam.axis == axis and (seam.tile_a if upper else seam.tile_b) == tile.name:
                mask[tuple(slice(seam.box[2 * o] - tile.lo(o), seam.box[2 * o + 1] - tile.lo(o)) for o in others)] = True
        cover.append(mask)
    return cover


def assemble_mask(window, parts) -> np.ndarray:
    """Boolean mask of ``window`` (region coordinates) that is true where any
    ``(tile, volume, local_ids)`` part holds one of its local ids. Each tile
    contributes one read: the part of the window inside it."""
    mask = np.zeros([window[2 * a + 1] - window[2 * a] for a in range(3)], bool)
    for tile, volume, local_ids in parts:
        inter = [(max(window[2 * a], tile.lo(a)), min(window[2 * a + 1], tile.hi(a))) for a in range(3)]
        if any(hi <= lo for lo, hi in inter):
            continue
        block = np.asarray(volume[tuple(slice(lo - tile.lo(a), hi - tile.lo(a)) for a, (lo, hi) in enumerate(inter))])
        dst = tuple(slice(lo - window[2 * a], hi - window[2 * a]) for a, (lo, hi) in enumerate(inter))
        mask[dst] |= block == local_ids[0] if len(local_ids) == 1 else np.isin(block, local_ids)
    return mask


# --- inputs ------------------------------------------------------------------

def read_unproofread(path, tiles: list[Tile]) -> set[str]:
    """Tile names listed (by name or path, matched on the box) in ``path``."""
    by_box = {t.box: t.name for t in tiles}
    names, unknown = set(), []
    for lineno, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            _, box = parse_tile_name(line)
        except StitchingError as exc:
            raise StitchingError(f"{path}:{lineno}: {exc}") from None
        if box not in by_box:
            unknown.append(f"{path}:{lineno}: {line}")
            continue
        names.add(by_box[box])
    if unknown:
        raise StitchingError("unproofread tiles not in tiles.csv:\n" + "\n".join(f"  {u}" for u in unknown))
    return names


def load_objects(work: Path, id_rows: list[dict], resolved: list[dict]) -> dict[int, int]:
    """``{global_id: object_id}`` from objects.csv, checked against id_map.csv
    and the decisions phase 5 used."""
    object_of = {int(r["global_id"]): int(r["object_id"]) for r in read_csv(work / "objects.csv")}
    if set(object_of) != {int(r["global_id"]) for r in id_rows}:
        raise StitchingError("objects.csv does not cover exactly the ids in id_map.csv; rerun stitch_objects")
    groups: dict[int, list[int]] = {}
    for g, o in object_of.items():
        groups.setdefault(o, []).append(g)
    if any(min(members) != o for o, members in groups.items()):
        raise StitchingError("objects.csv: an object_id is not its smallest member; rerun stitch_objects")
    for row in resolved:
        if row["decision"] == "yes" and object_of[int(row["global_a"])] != object_of[int(row["global_b"])]:
            raise StitchingError("objects.csv does not match decisions_resolved.csv; rerun stitch_objects")
    return object_of


def load_resolved(work: Path, candidates: list[dict]) -> list[dict]:
    resolved = read_csv(work / "decisions_resolved.csv")
    key = lambda r: (r["seam"], r["tile_a"], r["label_a"], r["tile_b"], r["label_b"])  # noqa: E731
    if sorted(map(key, resolved)) != sorted(map(key, candidates)):
        raise StitchingError("decisions_resolved.csv does not match candidates.csv; rerun stitch_objects")
    return resolved


# --- measuring ---------------------------------------------------------------

@dataclass
class MeasureResult:
    objects: list[StitchedObject]
    rows: list[dict]
    counts: dict[str, int]
    fields: tuple[str, ...] = MEASUREMENTS_FIELDS
    unresolved: list[dict] = field(default_factory=list)   # rows for unresolved_candidates.csv
    label_boxes: list[dict] = field(default_factory=list)  # rows for label_boxes.csv
    run: dict = field(default_factory=dict)                 # measure_run.json


def measure_objects(work, voxel_size_zyx, *, unproofread=None, max_crop_voxels: int = DEFAULT_MAX_CROP_VOXELS,
                    region_only: bool = False, log=lambda message: None) -> MeasureResult:
    """Measure every object, or with ``region_only`` only objects reaching into
    the region (total ``region_voxels`` > 0; needs ``stitch_ids --regions``)."""
    work = Path(work)
    try:
        voxel_size = validate_voxel_size(voxel_size_zyx)
    except MeasurementError as exc:
        raise StitchingError(str(exc)) from None
    if max_crop_voxels < 1:
        raise StitchingError("--max-crop-voxels must be positive")
    inventory = build_inventory(load_tiles_csv(work))
    require_stitchable(inventory)
    check_seams_csv(work, inventory)
    tile_ids = load_tile_ids(work, inventory.tiles)
    tiles = inventory.tiles
    order = {t.name: i for i, t in enumerate(tiles)}
    candidates = read_csv(work / "candidates.csv")
    id_rows = read_csv(work / "id_map.csv")
    resolved = load_resolved(work, candidates)
    object_of = load_objects(work, id_rows, resolved)
    unproofread_names = read_unproofread(unproofread, tiles) if unproofread else set()
    region_voxels = load_region_voxels(work)
    if region_only and region_voxels is None:
        raise StitchingError("--region-only needs region data; run stitch_ids --regions and the later phases")

    member = {int(r["global_id"]): (r["tile"], int(r["local_id"]), int(r["voxel_count"])) for r in id_rows}
    members_in: dict[str, set[int]] = {}
    for g, (name, _, _) in member.items():
        members_in.setdefault(name, set()).add(g)
    groups: dict[int, list[int]] = {}
    for g in sorted(object_of):
        groups.setdefault(object_of[g], []).append(g)
    objects = {}
    for object_id, members in sorted(groups.items()):
        names = sorted({member[g][0] for g in members}, key=order.__getitem__)
        objects[object_id] = StitchedObject(
            object_id, members, names, sum(member[g][2] for g in members),
            unproofread_tiles=sum(n in unproofread_names for n in names),
            region_voxels=None if region_voxels is None else sum(region_voxels[g] for g in members))
    # An unsure/undecided candidate is unresolved for both objects it joins. In
    # region-only mode it counts when either *object* (after phase 5 merges)
    # reaches into the region: a merged A+B with A inside is truncated if its
    # B-C pair is undecided, even though neither B nor C has region voxels.
    decision_of = {(r["seam"], r["label_a"], r["label_b"]): r["decision"] for r in resolved}
    unresolved_rows = []
    for c in candidates:
        decision = decision_of[(c["seam"], c["label_a"], c["label_b"])]
        if decision not in UNRESOLVED:
            continue
        object_a, object_b = object_of[int(c["global_a"])], object_of[int(c["global_b"])]
        if region_only and not (objects[object_a].region_voxels or objects[object_b].region_voxels):
            continue
        for o in {object_a, object_b}:
            objects[o].unresolved_contacts += 1
        unresolved_rows.append({**{f: c[f] for f in UNRESOLVED_FIELDS if f in c}, "object_a": object_a,
                                "object_b": object_b, "current_decision": decision})
    if region_only:
        objects = {o: obj for o, obj in objects.items() if obj.region_voxels > 0}

    region = region_box(tiles)
    boxes: dict[int, tuple[int, ...]] = {}
    single = {o.members[0]: o for o in objects.values() if len(o.members) == 1}
    started = time.monotonic()

    # Pass 1: one read of every tile for label boxes, edges and single-tile objects.
    for index, tile in enumerate(tiles, 1):
        t0 = time.monotonic()
        offset = tile_ids[tile.name]["id_offset"]
        cover = face_coverage(tile, inventory.seams)
        measured = skipped = 0
        with open_tile(tile.path) as volume:
            if tuple(volume.shape) != tile.shape:
                raise StitchingError(f"{tile.path}: shape {tuple(volume.shape)} does not match tiles.csv")
            summary = _derive_summary(_scan_stats(volume))
            counts = {entry["id"]: entry["voxel_count"] for entry in summary["labels"]}
            planes: dict[int, np.ndarray] = {}
            for lid, local in summary["bboxes"].items():
                g = offset + int(lid)
                if member.get(g, (None, None, None))[:2] != (tile.name, int(lid)) or member[g][2] != counts[lid]:
                    raise StitchingError(f"{tile.name}: label {lid} does not match id_map.csv; rerun stitch_ids")
                boxes[g] = tuple(v + tile.lo(a // 2) for a, v in enumerate(local))
                for face in range(6):
                    if object_of[g] in objects and _touches_uncovered(volume, tile, int(lid), local, face,
                                                                      cover[face], planes):
                        objects[object_of[g]].edge_faces.add((order[tile.name], face, tile.name))
            if any(g not in boxes for g in members_in.get(tile.name, ())):
                raise StitchingError(f"{tile.name}: id_map.csv lists labels the tile lacks; rerun stitch_ids")
            for lid in summary["bboxes"]:
                obj = single.get(offset + int(lid))
                if obj is None:
                    continue
                obj.box = boxes[obj.members[0]]
                window = padded_window(obj.box, region)
                parts = [(tile, volume, [int(lid)])]
                if box_voxels(window) > max_crop_voxels:
                    obj.skipped_reason = SKIPPED_CROP_LIMIT
                    _set_moments(obj, window_moments(window, parts, max_crop_voxels))
                    skipped += 1
                    continue
                mask = assemble_mask(window, parts)
                _set_moments(obj, mask_moments(mask, window))
                obj.measure(mask, voxel_size)
                measured += 1
        log(f"tile {index}/{len(tiles)} {tile.name}: {len(summary['bboxes'])} label(s), "
            f"{measured} single-tile object(s) measured, {skipped} skipped (crop_limit) "
            f"in {time.monotonic() - t0:.1f}s")

    # Pass 2: assemble and skeletonise each multi-tile object.
    by_name = {t.name: t for t in tiles}
    multi = [o for o in objects.values() if len(o.members) > 1]
    for index, obj in enumerate(multi, 1):
        t0 = time.monotonic()
        obj.box = box_union(boxes[g] for g in obj.members)
        window = padded_window(obj.box, region)
        size = "x".join(str(window[2 * a + 1] - window[2 * a]) for a in range(3))
        locals_by_tile: dict[str, list[int]] = {}
        for g in obj.members:
            locals_by_tile.setdefault(member[g][0], []).append(member[g][1])
        too_large = box_voxels(window) > max_crop_voxels
        with ExitStack() as stack:
            parts = [(by_name[n], stack.enter_context(open_tile(by_name[n].path)), locals_by_tile[n])
                     for n in obj.tiles]
            if too_large:   # no skeleton, but the centroid is still needed: read the window in slabs
                _set_moments(obj, window_moments(window, parts, max_crop_voxels))
            else:
                mask = assemble_mask(window, parts)
                _set_moments(obj, mask_moments(mask, window))
        if too_large:
            obj.skipped_reason = SKIPPED_CROP_LIMIT
            log(f"object {index}/{len(multi)} {obj.object_id}: {len(obj.tiles)} tiles, crop {size} "
                f"= {box_voxels(window)} voxels > {max_crop_voxels}; skipped (crop_limit)")
            continue
        obj.measure(mask, voxel_size)
        log(f"object {index}/{len(multi)} {obj.object_id}: {len(obj.members)} label(s) in {len(obj.tiles)} tiles, "
            f"crop {size}, {obj.skeleton_length_um:.4g} um in {time.monotonic() - t0:.1f}s")

    ordered = [objects[o] for o in sorted(objects)]
    counts = {
        "region_only": region_only,
        "objects": len(ordered), "multi_tile": len(multi),
        "touches_edge": sum(1 for o in ordered if o.edge_faces),
        "skipped": sum(1 for o in ordered if o.skipped_reason),
        "unresolved": sum(1 for o in ordered if o.unresolved_contacts),
        "unproofread": sum(1 for o in ordered if o.unproofread_tiles),
        "crosses_region": sum(1 for o in ordered if o.region_voxels and o.region_voxels < o.voxel_count),
        "seconds": round(time.monotonic() - started),
    }
    fields = MEASUREMENTS_FIELDS + (REGION_FIELDS if region_voxels is not None else ())
    label_boxes = [{"global_id": g, "tile": member[g][0], "local_id": member[g][1],
                    **dict(zip(LABEL_BOXES_FIELDS[3:], boxes[g]))} for g in sorted(boxes)]
    run = {
        "voxel_size_zyx_nm": list(voxel_size), "region_only": region_only, "max_crop_voxels": max_crop_voxels,
        "unproofread_tiles": [t.name for t in tiles if t.name in unproofread_names],
        "code_revision": code_revision(), "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return MeasureResult(ordered, [o.row(voxel_size) for o in ordered], counts, fields, unresolved_rows,
                         label_boxes, run)


def _touches_uncovered(volume, tile: Tile, lid: int, local_box, face: int, covered: np.ndarray, planes) -> bool:
    """Whether label ``lid`` (local box ``local_box``) has a voxel on ``face``
    of ``tile`` at a position no seam covers."""
    axis, upper = divmod(face, 2)
    on_face = local_box[2 * axis + 1] == tile.shape[axis] if upper else local_box[2 * axis] == 0
    if not on_face or covered.all():
        return False
    if not covered.any():
        return True
    if face not in planes:  # partly covered face: check the label's own footprint
        key = tuple((tile.shape[a] - 1 if upper else 0) if a == axis else slice(None) for a in range(3))
        planes[face] = np.asarray(volume[key])
    return bool(((planes[face] == lid) & ~covered).any())


def run_measure(work, voxel_size_zyx, *, unproofread=None, max_crop_voxels: int = DEFAULT_MAX_CROP_VOXELS,
                region_only: bool = False, log=lambda message: None, command: str = "") -> MeasureResult:
    result = measure_objects(work, voxel_size_zyx, unproofread=unproofread, max_crop_voxels=max_crop_voxels,
                             region_only=region_only, log=log)
    work = Path(work)
    write_csv(work / "measurements.csv", result.fields, result.rows)
    write_csv(work / "unresolved_candidates.csv", UNRESOLVED_FIELDS, result.unresolved)
    write_csv(work / "label_boxes.csv", LABEL_BOXES_FIELDS, result.label_boxes)
    result.run["command"] = command
    (work / MEASURE_RUN).write_text(json.dumps(result.run, indent=2) + "\n")
    return result


def load_measure_run(work) -> dict:
    path = Path(work) / MEASURE_RUN
    if not path.exists():
        raise StitchingError(f"{path} not found; rerun stitch_measure (phase 6)")
    return json.loads(path.read_text())
