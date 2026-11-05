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

import math
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from annotation.cellable_port.labels_3d import _derive_summary, _scan_stats
from annotation.measurements import (
    CROP_PAD,
    SKIPPED_CROP_LIMIT,
    MeasurementError,
    skeleton_length_nm,
    validate_voxel_size,
)
from annotation.stitching import (
    REGION_VOXELS,
    StitchingError,
    Tile,
    build_inventory,
    check_seams_csv,
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
MEASUREMENTS_FIELDS = ("object_id", "member_count", "tiles", "voxel_count", "volume_um3", "skeleton_length_um",
                       "touches_edge", "edge_faces", "skipped_reason", "unresolved_contacts", "unproofread_tiles")
REGION_FIELDS = (REGION_VOXELS, "frac_in_region", "crosses_region_boundary")
UNRESOLVED = ("unsure", "undecided")


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

    def row(self, voxel_nm3: float) -> dict:
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
            "unproofread_tiles": self.unproofread_tiles, **region,
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
    for row in resolved:
        pair = (int(row["global_a"]), int(row["global_b"]))
        if region_only and not any(region_voxels[g] > 0 for g in pair):
            continue  # out-of-region candidates are not part of a region-only analysis
        if row["decision"] in UNRESOLVED:
            for o in {object_of[g] for g in pair}:
                objects[o].unresolved_contacts += 1
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
                window = padded_window(boxes[obj.members[0]], region)
                if box_voxels(window) > max_crop_voxels:
                    obj.skipped_reason = SKIPPED_CROP_LIMIT
                    skipped += 1
                    continue
                mask = assemble_mask(window, [(tile, volume, [int(lid)])])
                obj.skeleton_length_um = skeleton_length_nm(mask, voxel_size) / 1000.0
                measured += 1
        log(f"tile {index}/{len(tiles)} {tile.name}: {len(summary['bboxes'])} label(s), "
            f"{measured} single-tile object(s) measured, {skipped} skipped (crop_limit) "
            f"in {time.monotonic() - t0:.1f}s")

    # Pass 2: assemble and skeletonise each multi-tile object.
    by_name = {t.name: t for t in tiles}
    multi = [o for o in objects.values() if len(o.members) > 1]
    for index, obj in enumerate(multi, 1):
        t0 = time.monotonic()
        window = padded_window(box_union(boxes[g] for g in obj.members), region)
        size = "x".join(str(window[2 * a + 1] - window[2 * a]) for a in range(3))
        if box_voxels(window) > max_crop_voxels:
            obj.skipped_reason = SKIPPED_CROP_LIMIT
            log(f"object {index}/{len(multi)} {obj.object_id}: {len(obj.tiles)} tiles, crop {size} "
                f"= {box_voxels(window)} voxels > {max_crop_voxels}; skipped (crop_limit)")
            continue
        locals_by_tile: dict[str, list[int]] = {}
        for g in obj.members:
            locals_by_tile.setdefault(member[g][0], []).append(member[g][1])
        with ExitStack() as stack:
            parts = [(by_name[n], stack.enter_context(open_tile(by_name[n].path)), locals_by_tile[n])
                     for n in obj.tiles]
            mask = assemble_mask(window, parts)
        obj.skeleton_length_um = skeleton_length_nm(mask, voxel_size) / 1000.0
        log(f"object {index}/{len(multi)} {obj.object_id}: {len(obj.members)} label(s) in {len(obj.tiles)} tiles, "
            f"crop {size}, {obj.skeleton_length_um:.4g} um in {time.monotonic() - t0:.1f}s")

    voxel_nm3 = voxel_size[0] * voxel_size[1] * voxel_size[2]
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
    return MeasureResult(ordered, [o.row(voxel_nm3) for o in ordered], counts, fields)


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
                region_only: bool = False, log=lambda message: None) -> MeasureResult:
    result = measure_objects(work, voxel_size_zyx, unproofread=unproofread, max_crop_voxels=max_crop_voxels,
                             region_only=region_only, log=log)
    write_csv(Path(work) / "measurements.csv", result.fields, result.rows)
    return result
