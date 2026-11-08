"""Cross-tile mitochondria stitching, phases 1-3 (offline).

A region delivered as abutting label tiles is stitched by:

1. inventory — parse tile names into boxes, find shared faces (seams), and
   report overlaps, gaps, duplicates and missing grid positions;
2. global IDs — give every (tile, local label) a unique uint64-range ID using
   deterministic per-tile offsets;
3. candidates — for each seam, pair labels that touch across the two facing
   planes.

Nothing here touches the database or modifies a tile. The design, file formats
and the later phases are in ``docs/engineering/stitching.md``.
"""

from __future__ import annotations

import csv
import itertools
import os
import random
import re
import subprocess
from collections import Counter
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from annotation.visualization.slice_io import open_label_volume_readonly

AXES = ("z", "y", "x")
LABEL_SUFFIXES = (".nii.gz", ".tif", ".tiff", ".h5", ".hdf5", ".nii", ".npy")
TIFF_SUFFIXES = (".tif", ".tiff")
MAX_GLOBAL_ID = 2**64 - 1
# Bytes per Z block when phase 2 streams a tile.
Z_BLOCK_BYTES = 64 * 1024 * 1024

_NAME_RE = re.compile(r"^(\d+)-(\d+)_(\d+)-(\d+)_(\d+)-(\d+)(?:_(.*))?$")

TILES_FIELDS = ("tile", "path", "z0", "z1", "y0", "y1", "x0", "x1",
                "grid_z", "grid_y", "grid_x", "shape_zyx", "shape_ok")
SEAMS_FIELDS = ("seam", "axis", "direction", "tile_a", "tile_b", "position",
                "z0", "z1", "y0", "y1", "x0", "x1", "face_voxels")
ISSUES_FIELDS = ("kind", "axis", "tile_a", "tile_b", "z0", "z1", "y0", "y1", "x0", "x1", "detail")
TILE_IDS_FIELDS = ("tile", "id_offset", "max_local_id", "label_count", "labelled_voxels",
                   "dtype", "file_size", "file_mtime_ns")
ID_MAP_FIELDS = ("tile", "local_id", "global_id", "voxel_count")
CANDIDATES_FIELDS = ("seam", "axis", "tile_a", "label_a", "tile_b", "label_b", "global_a", "global_b",
                     "contact_voxels", "area_a", "area_b", "frac_a", "frac_b")
DECISION_FIELDS = ("seam", "tile_a", "label_a", "tile_b", "label_b", "global_a", "global_b",
                   "decision", "reviewer", "decided_at")
DECISION_VALUES = ("yes", "no", "unsure")
RESOLVED_FIELDS = ("seam", "tile_a", "label_a", "tile_b", "label_b", "global_a", "global_b", "decision")
OBJECTS_FIELDS = ("global_id", "object_id")
AUTO_SAMPLE_FIELDS = ("seam", "tile_a", "label_a", "tile_b", "label_b", "global_a", "global_b",
                      "contact_voxels", "frac_a", "frac_b")
OBJECTS_SUMMARY_FIELDS = ("object_id", "member_count", "tiles", "total_voxel_count", "seam_count")
# Region (ROI) columns, present only when stitch_ids ran with --regions.
REGION_VOXELS = "region_voxels"
IN_REGION = "in_region"
REGION_PAIRING_FIELDS = ("tile", "region_tile", "region_path", "status")
# How many problem rows a refusal lists before summarising the rest.
MAX_LISTED = 50


class StitchingError(Exception):
    pass


# --- names and boxes ---------------------------------------------------------

def strip_label_suffix(filename: str) -> str:
    """``filename`` without a recognised label-file extension."""
    lower = filename.lower()
    for suffix in LABEL_SUFFIXES:
        if lower.endswith(suffix):
            return filename[: -len(suffix)]
    return filename


def parse_tile_name(name: str) -> tuple[str, tuple[int, int, int, int, int, int]]:
    """Return ``(tile, (z0, z1, y0, y1, x0, x1))`` for a tile name or path.

    ``tile`` is the file name without directory or label extension. The name
    must start with ``z0-z1_y0-y1_x0-x1`` (half-open voxel bounds), optionally
    followed by ``_<suffix>``.
    """
    tile = strip_label_suffix(Path(name).name)
    match = _NAME_RE.match(tile)
    if not match:
        raise StitchingError(f"tile name {name!r} does not start with z0-z1_y0-y1_x0-x1")
    box = tuple(int(v) for v in match.groups()[:6])
    for axis, (lo, hi) in zip(AXES, zip(box[0::2], box[1::2])):
        if hi <= lo:
            raise StitchingError(f"tile name {name!r}: empty {axis} range {lo}-{hi}")
    return tile, box


@dataclass(frozen=True)
class Tile:
    name: str
    box: tuple[int, int, int, int, int, int]
    path: str = ""

    def lo(self, axis: int) -> int:
        return self.box[2 * axis]

    def hi(self, axis: int) -> int:
        return self.box[2 * axis + 1]

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple(self.hi(a) - self.lo(a) for a in range(3))

    @property
    def sort_key(self):
        return (self.box[0], self.box[2], self.box[4], self.name)


@dataclass(frozen=True)
class Seam:
    seam: int
    axis: int
    tile_a: str
    tile_b: str
    position: int
    box: tuple[int, int, int, int, int, int]

    @property
    def face_voxels(self) -> int:
        return int(np.prod([self.box[2 * a + 1] - self.box[2 * a] for a in range(3) if a != self.axis]))


@dataclass(frozen=True)
class Issue:
    kind: str
    axis: str = ""
    tile_a: str = ""
    tile_b: str = ""
    box: tuple | None = None
    detail: str = ""

    def row(self) -> dict:
        box = self.box or ("",) * 6
        return {"kind": self.kind, "axis": self.axis, "tile_a": self.tile_a, "tile_b": self.tile_b,
                **dict(zip(("z0", "z1", "y0", "y1", "x0", "x1"), box)), "detail": self.detail}


@dataclass
class Inventory:
    tiles: list[Tile]
    seams: list[Seam]
    issues: list[Issue]
    grid: dict[str, dict[str, int]] = field(default_factory=dict)  # tile -> {"z": i, ...}
    shapes: dict[str, tuple[int, int, int] | None] = field(default_factory=dict)

    def issues_of(self, *kinds: str) -> list[Issue]:
        return [i for i in self.issues if i.kind in kinds]


# --- collecting tiles --------------------------------------------------------

def collect_tiles(source) -> tuple[list[Tile], list[Issue]]:
    """Tiles from a directory of label files or a text file of names/paths."""
    source = Path(source)
    if source.is_dir():
        return _tiles_from_directory(source)
    if source.is_file():
        return _tiles_from_list(source)
    raise StitchingError(f"tile source not found: {source}")


def _tiles_from_directory(directory: Path) -> tuple[list[Tile], list[Issue]]:
    tiles, issues = [], []
    for entry in sorted(directory.iterdir()):
        if entry.name.startswith(".") or not entry.is_file():
            continue
        if strip_label_suffix(entry.name) == entry.name:
            continue  # not a label file
        try:
            name, box = parse_tile_name(entry.name)
        except StitchingError as exc:
            issues.append(Issue("unparsed_name", detail=str(exc)))
            continue
        tiles.append(Tile(name, box, str(entry.resolve())))
    return tiles, issues


def _tiles_from_list(list_file: Path) -> tuple[list[Tile], list[Issue]]:
    tiles = []
    for lineno, raw in enumerate(list_file.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            name, box = parse_tile_name(line)
        except StitchingError as exc:
            raise StitchingError(f"{list_file}:{lineno}: {exc}") from None
        candidate = Path(line)
        if not candidate.is_absolute():
            candidate = list_file.parent / candidate
        path = str(candidate.resolve()) if candidate.is_file() else ""
        tiles.append(Tile(name, box, path))
    return tiles, []


# --- phase 1: inventory ------------------------------------------------------

def _overlap(a_lo, a_hi, b_lo, b_hi) -> tuple[int, int] | None:
    lo, hi = max(a_lo, b_lo), min(a_hi, b_hi)
    return (lo, hi) if hi > lo else None


def find_seams(tiles: list[Tile]) -> list[Seam]:
    """Every pair of tiles sharing a face with positive area, canonicalised so
    ``tile_a`` is the lower tile on the seam axis."""
    found = []
    for a, b in itertools.combinations(tiles, 2):
        for axis in range(3):
            if a.hi(axis) == b.lo(axis):
                lower, upper = a, b
            elif b.hi(axis) == a.lo(axis):
                lower, upper = b, a
            else:
                continue
            spans = []
            for other in range(3):
                if other == axis:
                    spans.append((upper.lo(axis), upper.lo(axis)))
                    continue
                span = _overlap(a.lo(other), a.hi(other), b.lo(other), b.hi(other))
                if span is None:
                    break
                spans.append(span)
            else:
                found.append((lower, upper, axis, tuple(v for span in spans for v in span)))
    found.sort(key=lambda s: (s[0].sort_key, s[2], s[1].sort_key))
    return [Seam(i, axis, lower.name, upper.name, lower.hi(axis), box)
            for i, (lower, upper, axis, box) in enumerate(found)]


def _pair_issues(tiles: list[Tile]) -> list[Issue]:
    issues = []
    for a, b in itertools.combinations(tiles, 2):
        if a.name == b.name or a.box == b.box:
            issues.append(Issue("duplicate", tile_a=a.name, tile_b=b.name, box=a.box,
                                detail=f"{a.path or a.name} and {b.path or b.name}"))
            continue
        spans = [_overlap(a.lo(x), a.hi(x), b.lo(x), b.hi(x)) for x in range(3)]
        if all(spans):
            box = tuple(v for span in spans for v in span)
            voxels = int(np.prod([hi - lo for lo, hi in spans]))
            issues.append(Issue("overlap", tile_a=a.name, tile_b=b.name, box=box,
                                detail=f"{voxels} voxels"))
    return issues


def _grid(tiles: list[Tile]) -> tuple[dict[str, dict[str, int]], list[Issue]]:
    """Per-axis interval index of every tile, gaps and missing grid positions."""
    issues = []
    intervals: dict[int, list[tuple[int, int]] | None] = {}
    for axis, label in enumerate(AXES):
        spans = sorted({(t.lo(axis), t.hi(axis)) for t in tiles})
        if any(nxt[0] < cur[1] for cur, nxt in zip(spans, spans[1:])):
            issues.append(Issue("irregular_axis", axis=label,
                                detail="tile intervals on this axis overlap: "
                                       + " ".join(f"{lo}-{hi}" for lo, hi in spans)))
            intervals[axis] = None
            continue
        intervals[axis] = spans
        for cur, nxt in zip(spans, spans[1:]):
            if cur[1] < nxt[0]:
                issues.append(Issue("gap", axis=label, detail=f"{label} {cur[1]}-{nxt[0]} has no tiles"))

    grid = {}
    for t in tiles:
        grid[t.name] = {label: intervals[axis].index((t.lo(axis), t.hi(axis)))
                        for axis, label in enumerate(AXES) if intervals[axis] is not None}

    if all(intervals[axis] is not None for axis in range(3)):
        present = {t.box for t in tiles}
        for zs, ys, xs in itertools.product(*(intervals[axis] for axis in range(3))):
            box = (*zs, *ys, *xs)
            if box not in present:
                issues.append(Issue("missing_position", box=box,
                                    detail="grid position {}-{}_{}-{}_{}-{} has no tile".format(*box)))
    return grid, issues


def tile_shape(path) -> tuple[int, int, int]:
    """(Z, Y, X) shape of a tile file, read without loading voxel data."""
    with open_tile(path) as volume:
        return tuple(int(v) for v in volume.shape)


def build_inventory(tiles: list[Tile], issues: list[Issue] | None = None, *, read_shapes: bool = True) -> Inventory:
    tiles = sorted(tiles, key=lambda t: t.sort_key)
    issues = list(issues or [])
    issues += _pair_issues(tiles)
    grid, grid_issues = _grid(tiles)
    issues += grid_issues

    shapes: dict[str, tuple[int, int, int] | None] = {}
    for t in tiles:
        shapes[t.name] = None
        if not t.path:
            issues.append(Issue("no_file", tile_a=t.name, box=t.box, detail="no file for this tile name"))
            continue
        if not read_shapes:
            continue
        try:
            shapes[t.name] = tile_shape(t.path)
        except Exception as exc:  # any reader failure is reported, not fatal, in phase 1
            issues.append(Issue("unreadable", tile_a=t.name, box=t.box, detail=f"{t.path}: {exc}"))
            continue
        if shapes[t.name] != t.shape:
            issues.append(Issue("shape_mismatch", tile_a=t.name, box=t.box,
                                detail="file shape {} but name implies {}".format(
                                    _shape_text(shapes[t.name]), _shape_text(t.shape))))
    return Inventory(tiles, find_seams(tiles), issues, grid, shapes)


def _shape_text(shape) -> str:
    return "x".join(str(v) for v in shape) if shape else ""


def require_stitchable(inventory: Inventory) -> None:
    """Refuse phases 2-3 on an inventory whose tiles cannot be stitched."""
    blocking = inventory.issues_of("duplicate", "overlap", "no_file", "unreadable", "shape_mismatch")
    if blocking:
        lines = [f"  {i.kind}: {i.tile_a} {i.tile_b} {i.detail}".rstrip() for i in blocking[:20]]
        more = f"\n  ... and {len(blocking) - 20} more" if len(blocking) > 20 else ""
        raise StitchingError("tiles cannot be stitched until these are fixed:\n" + "\n".join(lines) + more)


# --- reading tiles -----------------------------------------------------------

class PagedTiff:
    """Lazy ``(Z, Y, X)`` view of a TIFF that cannot be memory-mapped.

    The shared reader falls back to decoding the whole file for those; this
    decodes only the pages (Z planes) an index touches, one at a time.
    """

    def __init__(self, path):
        import tifffile

        self._tif = tifffile.TiffFile(str(path))
        series = self._tif.series[0]
        shape = tuple(int(v) for v in series.shape)
        if len(shape) == 2:
            shape = (1, *shape)
        if len(shape) != 3 or len(series.pages) != shape[0]:
            self._tif.close()
            raise StitchingError(f"{Path(path).name}: unsupported TIFF layout {tuple(series.shape)}")
        self._pages = series.pages
        self.shape = shape
        self.dtype = np.dtype(series.dtype)

    def _page(self, z: int) -> np.ndarray:
        return self._pages[z].asarray().reshape(self.shape[1:])

    def __getitem__(self, key):
        if not isinstance(key, tuple):
            key = (key,)
        key = tuple(key) + (slice(None),) * (3 - len(key))
        z_key, rest = key[0], key[1:]
        if isinstance(z_key, (int, np.integer)):
            return self._page(range(self.shape[0])[int(z_key)])[rest]
        planes = [self._page(z)[rest] for z in range(self.shape[0])[z_key]]
        if not planes:
            return np.empty((0,) + np.empty(self.shape[1:])[rest].shape, dtype=self.dtype)
        return np.stack(planes)

    def close(self) -> None:
        self._tif.close()


def _tiff_is_memmappable(path: Path) -> bool:
    import tifffile

    with tifffile.TiffFile(str(path)) as tif:
        series = tif.series[0]
        return series.dataoffset is not None and not tif.pages[0].is_tiled


@contextmanager
def open_tile(path):
    """Lazy read-only ``(Z, Y, X)`` view of a tile file.

    Uses the application's label reader; a TIFF it could only read by decoding
    the whole file is read page by page instead.
    """
    path = Path(path)
    if path.suffix.lower() in TIFF_SUFFIXES and not _tiff_is_memmappable(path):
        volume = PagedTiff(path)
        try:
            yield volume
        finally:
            volume.close()
    else:
        yield open_label_volume_readonly(path)


def face_plane(volume, tile: Tile, seam: Seam, *, side: str) -> np.ndarray:
    """The plane of ``tile`` facing ``seam``, cropped to the seam's face.

    ``side="a"`` is tile A's last plane on the seam axis, ``"b"`` tile B's
    first. Only that plane is read.
    """
    key = []
    for axis in range(3):
        if axis == seam.axis:
            key.append(tile.shape[axis] - 1 if side == "a" else 0)
        else:
            lo = seam.box[2 * axis] - tile.lo(axis)
            hi = seam.box[2 * axis + 1] - tile.lo(axis)
            key.append(slice(lo, hi))
    return np.asarray(volume[tuple(key)])


def _check_label_dtype(dtype, where: str) -> None:
    dtype = np.dtype(dtype)
    if dtype == np.bool_ or not np.issubdtype(dtype, np.integer):
        raise StitchingError(f"{where}: labels must be integers, got {dtype}")


def count_labels(volume, *, where: str = "tile") -> dict[int, int]:
    """``{label: voxel_count}`` for every nonzero label, streaming Z blocks."""
    return _count_blocks(volume, None, where)[0]


def count_labels_in_region(volume, region, *, where: str = "tile") -> tuple[dict[int, int], dict[int, int], int]:
    """``(counts, inside, region_size)``: per-label voxel counts, per-label
    voxels where ``region`` (same shape, nonzero = inside) is set, and the
    region's size. Labels and region are read in the same Z blocks, so neither
    is ever held whole."""
    if tuple(region.shape) != tuple(volume.shape):
        raise StitchingError(f"{where}: region shape {tuple(region.shape)} differs from tile shape {tuple(volume.shape)}")
    return _count_blocks(volume, region, where)


def _count_blocks(volume, region, where: str):
    _check_label_dtype(volume.dtype, where)
    nz, ny, nx = volume.shape
    block = max(1, Z_BLOCK_BYTES // max(1, ny * nx * np.dtype(volume.dtype).itemsize))
    counts: dict[int, int] = {}
    inside: dict[int, int] = {}
    region_size = 0
    for z0 in range(0, nz, block):
        data = np.asarray(volume[z0 : z0 + block])
        if data.size and np.issubdtype(data.dtype, np.signedinteger) and data.min() < 0:
            raise StitchingError(f"{where}: negative label values")
        ids, n = np.unique(data, return_counts=True)
        for lid, c in zip(ids.tolist(), n.tolist()):
            if lid:
                counts[lid] = counts.get(lid, 0) + c
        if region is not None:
            mask = np.asarray(region[z0 : z0 + block]) != 0
            region_size += int(mask.sum())
            ids, n = np.unique(data[mask], return_counts=True)
            for lid, c in zip(ids.tolist(), n.tolist()):
                if lid:
                    inside[lid] = inside.get(lid, 0) + c
    return counts, inside, region_size


# --- pairing companion files by box ------------------------------------------

def pair_by_box(tiles: list[Tile], directories, *, what: str) -> tuple[dict[str, Tile | None], list[Tile]]:
    """Match each tile to the file in ``directories`` with the same box.

    Returns ``({tile name: matching file or None}, files matching no tile)``.
    Suffixes after the box are ignored (``..._im_xy.h5`` pairs with
    ``..._mask_pc2.h5``). Two files with one box, or a file whose shape differs
    from its tile, are errors.
    """
    by_box: dict[tuple, Tile] = {}
    for directory in directories:
        directory = Path(directory)
        if not directory.is_dir():
            raise StitchingError(f"{what} directory not found: {directory}")
        found, _ = collect_tiles(directory)
        for item in found:
            if item.box in by_box:
                raise StitchingError(
                    "two {} tiles share box {}-{}_{}-{}_{}-{}: ".format(what, *item.box)
                    + f"{by_box[item.box].path} and {item.path}")
            by_box[item.box] = item
    pairing = {t.name: by_box.get(t.box) for t in tiles}
    boxes = {t.box for t in tiles}
    unused = sorted((item for item in by_box.values() if item.box not in boxes), key=lambda t: t.sort_key)
    for tile in tiles:
        item = pairing[tile.name]
        if item is None:
            continue
        shape = tile_shape(item.path)
        if shape != tile.shape:
            raise StitchingError(
                f"{what} {item.path} has shape {'x'.join(map(str, shape))} "
                f"but tile {tile.name} is {'x'.join(map(str, tile.shape))}")
    return pairing, unused


def pairing_rows(tiles: list[Tile], pairing: dict[str, Tile | None], unused: list[Tile], *, what: str,
                 unused_status: str) -> list[dict]:
    """One row per tile (``matched`` / ``no_<what>``), then one per unused file."""
    rows = []
    for t in tiles:
        item = pairing[t.name]
        rows.append({"tile": t.name, f"{what}_tile": item.name if item else "",
                     f"{what}_path": item.path if item else "", "status": "matched" if item else f"no_{what}"})
    rows += [{"tile": "", f"{what}_tile": item.name, f"{what}_path": item.path, "status": unused_status}
             for item in unused]
    return rows


# --- phase 2: global IDs -----------------------------------------------------

def _file_fingerprint(path) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


def assign_global_ids(inventory: Inventory, regions: dict[str, Tile | None] | None = None) -> tuple[list[dict], list[dict]]:
    """Return ``(tile_ids_rows, id_map_rows)``: per-tile offsets, then one row per
    (tile, nonzero local label). See docs/engineering/stitching.md, phase 2.

    With ``regions`` ({tile name: paired region file or None}), both also get
    ``region_voxels``: per label, its voxels inside the region; per tile, the
    region's size. A tile without a region file is entirely outside."""
    require_stitchable(inventory)
    tile_rows, id_rows = [], []
    offset = 0
    for tile in inventory.tiles:
        inside, region_size = {}, 0
        with ExitStack() as stack:
            volume = stack.enter_context(open_tile(tile.path))
            region = regions.get(tile.name) if regions is not None else None
            if region is not None:
                counts, inside, region_size = count_labels_in_region(
                    volume, stack.enter_context(open_tile(region.path)), where=tile.name)
            else:
                counts = count_labels(volume, where=tile.name)
            dtype = np.dtype(volume.dtype).name
        max_local = max(counts, default=0)
        if offset + max_local > MAX_GLOBAL_ID:
            raise StitchingError(f"global ID space exceeds uint64 at tile {tile.name}")
        size, mtime_ns = _file_fingerprint(tile.path)
        tile_rows.append({
            "tile": tile.name, "id_offset": offset, "max_local_id": max_local,
            "label_count": len(counts), "labelled_voxels": sum(counts.values()),
            "dtype": dtype, "file_size": size, "file_mtime_ns": mtime_ns,
            **({REGION_VOXELS: region_size} if regions is not None else {}),
        })
        for lid in sorted(counts):
            id_rows.append({"tile": tile.name, "local_id": lid, "global_id": offset + lid,
                            "voxel_count": counts[lid],
                            **({REGION_VOXELS: inside.get(lid, 0)} if regions is not None else {})})
        offset += max_local
    return tile_rows, id_rows


# --- phase 3: candidates -----------------------------------------------------

def _areas(plane: np.ndarray) -> dict[int, int]:
    ids, n = np.unique(plane[plane != 0], return_counts=True)
    return dict(zip(ids.tolist(), n.tolist()))


def plane_contacts(plane_a: np.ndarray, plane_b: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """``(label_a, label_b, contact, area_a, area_b)`` for every pair of
    nonzero labels sharing at least one position on two aligned planes."""
    if plane_a.shape != plane_b.shape:
        raise StitchingError(f"facing planes differ in shape: {plane_a.shape} vs {plane_b.shape}")
    both = (plane_a != 0) & (plane_b != 0)
    if not both.any():
        return []
    pairs = np.stack([plane_a[both].astype(np.uint64), plane_b[both].astype(np.uint64)], axis=1)
    uniq, contact = np.unique(pairs, axis=0, return_counts=True)
    area_a, area_b = _areas(plane_a), _areas(plane_b)
    return [(int(a), int(b), int(c), area_a[int(a)], area_b[int(b)])
            for (a, b), c in zip(uniq.tolist(), contact.tolist())]


def seam_candidates(inventory: Inventory, tile_ids: dict[str, dict],
                    region_voxels: dict[int, int] | None = None) -> list[dict]:
    """Candidate merges for every seam, sorted by seam, then contact descending.

    With ``region_voxels`` ({global id: voxels inside the region}), each row
    also gets ``in_region``: true if either label reaches into the region."""
    require_stitchable(inventory)
    by_name = {t.name: t for t in inventory.tiles}
    rows = []
    for seam in inventory.seams:
        tile_a, tile_b = by_name[seam.tile_a], by_name[seam.tile_b]
        with open_tile(tile_a.path) as vol_a:
            _check_label_dtype(vol_a.dtype, tile_a.name)
            plane_a = face_plane(vol_a, tile_a, seam, side="a")
        with open_tile(tile_b.path) as vol_b:
            _check_label_dtype(vol_b.dtype, tile_b.name)
            plane_b = face_plane(vol_b, tile_b, seam, side="b")
        ids_a, ids_b = tile_ids[tile_a.name], tile_ids[tile_b.name]
        found = []
        for la, lb, contact, area_a, area_b in plane_contacts(plane_a, plane_b):
            for tile, lid, ids in ((tile_a, la, ids_a), (tile_b, lb, ids_b)):
                if lid > ids["max_local_id"]:
                    raise StitchingError(
                        f"{tile.name}: label {lid} is above the max_local_id {ids['max_local_id']} "
                        "recorded by stitch_ids; rerun stitch_ids")
            found.append({
                "seam": seam.seam, "axis": AXES[seam.axis],
                "tile_a": tile_a.name, "label_a": la, "tile_b": tile_b.name, "label_b": lb,
                "global_a": ids_a["id_offset"] + la, "global_b": ids_b["id_offset"] + lb,
                "contact_voxels": contact, "area_a": area_a, "area_b": area_b,
                "frac_a": f"{contact / area_a:.6f}", "frac_b": f"{contact / area_b:.6f}",
            })
            if region_voxels is not None:
                inside = any(region_voxels.get(found[-1][g], 0) > 0 for g in ("global_a", "global_b"))
                found[-1][IN_REGION] = "true" if inside else "false"
        found.sort(key=lambda r: (-r["contact_voxels"], r["label_a"], r["label_b"]))
        rows += found
    return rows


# --- phase 5: objects --------------------------------------------------------

def _pair_key(row: dict) -> tuple:
    return (int(row["seam"]), row["tile_a"], int(row["label_a"]), row["tile_b"], int(row["label_b"]))


def _listing(title: str, lines: list[str]) -> str:
    shown = "\n".join(f"  {line}" for line in lines[:MAX_LISTED])
    more = f"\n  ... and {len(lines) - MAX_LISTED} more" if len(lines) > MAX_LISTED else ""
    return f"{title} ({len(lines)}):\n{shown}{more}"


def read_decisions(paths) -> list[dict]:
    """Rows from one or more decisions.csv files, each tagged with its source."""
    rows = []
    for path in paths:
        path = Path(path)
        file_rows = read_csv(path)
        if file_rows:
            missing = [f for f in DECISION_FIELDS if f not in file_rows[0]]
            if missing:
                raise StitchingError(f"{path}: missing column(s) {', '.join(missing)}")
        for lineno, row in enumerate(file_rows, 2):
            rows.append({**row, "_source": f"{path}:{lineno}"})
    return rows


def resolve_decisions(candidates: list[dict], decision_rows: list[dict]) -> dict[tuple, str]:
    """``{candidate key: decision}`` for every decided candidate.

    Blank decisions count as undecided. Refuses (listing every offender) rows
    that match no candidates.csv row, invalid decision values, and pairs given
    different decisions anywhere in the inputs.
    """
    by_key = {_pair_key(c): c for c in candidates}
    unknown, invalid = [], []
    seen: dict[tuple, dict[str, list[str]]] = {}
    for row in decision_rows:
        source = row["_source"]
        decision = (row.get("decision") or "").strip().lower()
        if not decision:
            continue
        if decision not in DECISION_VALUES:
            invalid.append(f"{source}: decision {row['decision']!r} is not yes, no or unsure")
            continue
        try:
            key = _pair_key(row)
            globals_ = (int(row["global_a"]), int(row["global_b"]))
        except (KeyError, ValueError):
            unknown.append(f"{source}: seam, label or global id columns are not integers")
            continue
        candidate = by_key.get(key)
        if candidate is None or globals_ != (int(candidate["global_a"]), int(candidate["global_b"])):
            unknown.append(f"{source}: seam {key[0]} {key[1]}:{key[2]} -> {key[3]}:{key[4]} "
                           f"(global {row.get('global_a')} -> {row.get('global_b')}) is not in candidates.csv")
            continue
        seen.setdefault(key, {}).setdefault(decision, []).append(source)

    conflicts = [
        f"seam {key[0]} {key[1]}:{key[2]} -> {key[3]}:{key[4]}: "
        + "; ".join(f"{d} at {', '.join(srcs)}" for d, srcs in sorted(decisions.items()))
        for key, decisions in seen.items() if len(decisions) > 1
    ]
    problems = []
    if unknown:
        problems.append(_listing("decisions that match no candidates.csv row", unknown))
    if invalid:
        problems.append(_listing("invalid decision values", invalid))
    if conflicts:
        problems.append(_listing("conflicting decisions for the same pair", conflicts))
    if problems:
        raise StitchingError("\n".join(problems))
    return {key: next(iter(decisions)) for key, decisions in seen.items()}


class UnionFind:
    """Union-find whose root is always the smallest member."""

    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            lo, hi = (ra, rb) if ra < rb else (rb, ra)
            self.parent[hi] = lo


@dataclass
class ObjectsResult:
    objects: list[dict]
    summary: list[dict]
    counts: dict[str, int]
    repeated_tile_objects: list[int]  # objects holding two labels from one tile


def build_objects(tiles: list[Tile], id_rows: list[dict], candidates: list[dict],
                  decisions: dict[tuple, str]) -> ObjectsResult:
    order = {t.name: i for i, t in enumerate(tiles)}
    members = {int(r["global_id"]): (r["tile"], int(r["voxel_count"])) for r in id_rows}
    uf = UnionFind(sorted(members))
    counts = dict.fromkeys(DECISION_VALUES, 0)
    counts["undecided"] = 0
    merges = []
    for candidate in candidates:
        decision = decisions.get(_pair_key(candidate))
        counts[decision or "undecided"] += 1
        if decision != "yes":
            continue
        ga, gb = int(candidate["global_a"]), int(candidate["global_b"])
        for g in (ga, gb):
            if g not in members:
                raise StitchingError(f"global id {g} from candidates.csv is not in id_map.csv; rerun stitch_ids")
        uf.union(ga, gb)
        merges.append((int(candidate["seam"]), ga))

    objects = [{"global_id": g, "object_id": uf.find(g)} for g in sorted(members)]
    groups: dict[int, list[int]] = {}
    for row in objects:
        groups.setdefault(row["object_id"], []).append(row["global_id"])
    seams: dict[int, set[int]] = {}
    for seam, g in merges:
        seams.setdefault(uf.find(g), set()).add(seam)

    summary, repeated = [], []
    for object_id in sorted(groups):
        tile_names = [members[g][0] for g in groups[object_id]]
        distinct = sorted(set(tile_names), key=order.__getitem__)
        if len(distinct) < len(tile_names):
            repeated.append(object_id)
        summary.append({
            "object_id": object_id, "member_count": len(groups[object_id]), "tiles": ";".join(distinct),
            "total_voxel_count": sum(members[g][1] for g in groups[object_id]),
            "seam_count": len(seams.get(object_id, ())),
        })
    counts["objects"] = len(summary)
    counts["merged_objects"] = sum(1 for row in summary if row["member_count"] > 1)
    return ObjectsResult(objects, summary, counts, repeated)


def code_revision() -> str:
    """The git commit of this code, with ``+modified`` when the checkout has
    uncommitted changes, or ``unknown`` outside a git checkout."""
    here = Path(__file__).resolve().parent
    try:
        commit = subprocess.run(["git", "-C", str(here), "rev-parse", "HEAD"], capture_output=True, text=True,
                                check=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(here), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return commit + ("+modified" if dirty else "")


# --- CSV I/O -----------------------------------------------------------------

def write_csv(path, fields, rows) -> None:
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_csv(path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise StitchingError(f"{path} not found; run the earlier stitching phase first")
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def tiles_rows(inventory: Inventory) -> list[dict]:
    rows = []
    for t in inventory.tiles:
        grid = inventory.grid.get(t.name, {})
        shape = inventory.shapes.get(t.name)
        rows.append({
            "tile": t.name, "path": t.path,
            **dict(zip(("z0", "z1", "y0", "y1", "x0", "x1"), t.box)),
            "grid_z": grid.get("z", ""), "grid_y": grid.get("y", ""), "grid_x": grid.get("x", ""),
            "shape_zyx": _shape_text(shape),
            "shape_ok": "" if shape is None else ("true" if shape == t.shape else "false"),
        })
    return rows


def seams_rows(inventory: Inventory) -> list[dict]:
    return [{
        "seam": s.seam, "axis": AXES[s.axis], "direction": "+" + AXES[s.axis],
        "tile_a": s.tile_a, "tile_b": s.tile_b, "position": s.position,
        **dict(zip(("z0", "z1", "y0", "y1", "x0", "x1"), s.box)), "face_voxels": s.face_voxels,
    } for s in inventory.seams]


def _box(row: dict) -> tuple[int, ...]:
    return tuple(int(row[k]) for k in ("z0", "z1", "y0", "y1", "x0", "x1"))


def load_tiles_csv(work) -> list[Tile]:
    return [Tile(r["tile"], _box(r), r["path"]) for r in read_csv(Path(work) / "tiles.csv")]


def load_tile_ids(work, tiles: list[Tile]) -> dict[str, dict]:
    rows = read_csv(Path(work) / "tile_ids.csv")
    ids = {r["tile"]: {"id_offset": int(r["id_offset"]), "max_local_id": int(r["max_local_id"]),
                       "file_size": int(r["file_size"]), "file_mtime_ns": int(r["file_mtime_ns"])}
           for r in rows}
    if set(ids) != {t.name for t in tiles}:
        raise StitchingError("tile_ids.csv does not match tiles.csv; rerun stitch_ids")
    for t in tiles:
        if _file_fingerprint(t.path) != (ids[t.name]["file_size"], ids[t.name]["file_mtime_ns"]):
            raise StitchingError(f"{t.path} changed since stitch_ids; rerun stitch_ids")
    return ids


def check_seams_csv(work, inventory: Inventory) -> None:
    """Refuse a seams.csv that no longer matches tiles.csv."""
    rows = read_csv(Path(work) / "seams.csv")
    expected = [{k: str(v) for k, v in row.items()} for row in seams_rows(inventory)]
    if rows != expected:
        raise StitchingError("seams.csv does not match tiles.csv; rerun stitch_inventory")


# --- phase entry points ------------------------------------------------------

def run_inventory(source, work) -> Inventory:
    tiles, issues = collect_tiles(source)
    if not tiles:
        raise StitchingError(f"no tiles found in {source}")
    inventory = build_inventory(tiles, issues)
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    write_csv(work / "tiles.csv", TILES_FIELDS, tiles_rows(inventory))
    write_csv(work / "seams.csv", SEAMS_FIELDS, seams_rows(inventory))
    write_csv(work / "issues.csv", ISSUES_FIELDS, [i.row() for i in inventory.issues])
    return inventory


def run_ids(work, region_dirs=None) -> tuple[list[dict], list[dict], list[dict] | None]:
    """Phase 2. With ``region_dirs``, also count voxels inside each tile's
    region mask and write region_pairing.csv; returns its rows as the third item."""
    work = Path(work)
    inventory = build_inventory(load_tiles_csv(work))
    require_stitchable(inventory)
    regions = pair_rows = None
    if region_dirs:
        regions, unused = pair_by_box(inventory.tiles, region_dirs, what="region")
        pair_rows = pairing_rows(inventory.tiles, regions, unused, what="region", unused_status="no_tile")
    tile_rows, id_rows = assign_global_ids(inventory, regions)
    extra = (REGION_VOXELS,) if regions is not None else ()
    write_csv(work / "tile_ids.csv", TILE_IDS_FIELDS + extra, tile_rows)
    write_csv(work / "id_map.csv", ID_MAP_FIELDS + extra, id_rows)
    if pair_rows is None:
        (work / "region_pairing.csv").unlink(missing_ok=True)  # stale from an earlier --regions run
    else:
        write_csv(work / "region_pairing.csv", REGION_PAIRING_FIELDS, pair_rows)
    return tile_rows, id_rows, pair_rows


def csv_fields(path) -> list[str]:
    """Header of a CSV file (rows may be empty)."""
    path = Path(path)
    if not path.exists():
        raise StitchingError(f"{path} not found; run the earlier stitching phase first")
    with open(path, newline="") as fh:
        return next(csv.reader(fh), [])


def load_region_voxels(work) -> dict[int, int] | None:
    """``{global id: region_voxels}`` from id_map.csv, or None without region data."""
    path = Path(work) / "id_map.csv"
    if REGION_VOXELS not in csv_fields(path):
        return None
    return {int(r["global_id"]): int(r[REGION_VOXELS]) for r in read_csv(path)}


def require_in_region(work) -> None:
    if IN_REGION not in csv_fields(Path(work) / "candidates.csv"):
        raise StitchingError("candidates.csv has no in_region column; run stitch_ids --regions, "
                             "then stitch_candidates")


def run_candidates(work) -> list[dict]:
    work = Path(work)
    inventory = build_inventory(load_tiles_csv(work))
    require_stitchable(inventory)
    check_seams_csv(work, inventory)
    tile_ids = load_tile_ids(work, inventory.tiles)
    region_voxels = load_region_voxels(work)
    rows = seam_candidates(inventory, tile_ids, region_voxels)
    write_csv(work / "candidates.csv", CANDIDATES_FIELDS + ((IN_REGION,) if region_voxels is not None else ()), rows)
    return rows


def run_objects(work, decision_paths, *, require_complete: bool = False) -> ObjectsResult:
    work = Path(work)
    if not decision_paths:
        raise StitchingError("pass at least one decisions.csv")
    tiles = load_tiles_csv(work)
    candidates = read_csv(work / "candidates.csv")
    decisions = resolve_decisions(candidates, read_decisions(decision_paths))
    result = build_objects(sorted(tiles, key=lambda t: t.sort_key), read_csv(work / "id_map.csv"),
                           candidates, decisions)
    if require_complete and result.counts["undecided"]:
        raise StitchingError(f"{result.counts['undecided']} of {len(candidates)} candidate(s) are undecided "
                             "(--require-complete)")
    write_csv(work / "objects.csv", OBJECTS_FIELDS, result.objects)
    write_csv(work / "objects_summary.csv", OBJECTS_SUMMARY_FIELDS, result.summary)
    write_csv(work / "decisions_resolved.csv", RESOLVED_FIELDS,
              [{**{f: c[f] for f in RESOLVED_FIELDS[:-1]}, "decision": decisions.get(_pair_key(c), "undecided")}
               for c in candidates])
    return result


def one_to_one(candidates: list[dict]) -> list[bool]:
    """Per candidate row: is it the only row on its seam for both its labels?"""
    seen = Counter()
    for c in candidates:
        seen[(c["seam"], "a", c["label_a"])] += 1
        seen[(c["seam"], "b", c["label_b"])] += 1
    return [seen[(c["seam"], "a", c["label_a"])] == 1 and seen[(c["seam"], "b", c["label_b"])] == 1
            for c in candidates]


def auto_accept(candidates: list[dict], *, min_frac: float, min_contact: int,
                region_only: bool = False) -> list[int]:
    """Indices of candidates accepted by the auto rule: one-to-one on their
    seam, ``min(frac_a, frac_b) >= min_frac`` and ``contact_voxels >= min_contact``.

    One-to-one is judged against every candidate on the seam; ``region_only``
    then keeps only ``in_region`` rows, so a partner outside the region still
    disqualifies a pair."""
    return [i for i, (c, single) in enumerate(zip(candidates, one_to_one(candidates)))
            if single and min(float(c["frac_a"]), float(c["frac_b"])) >= min_frac
            and int(c["contact_voxels"]) >= min_contact
            and (not region_only or c[IN_REGION] == "true")]


def auto_rule_name(min_frac: float, min_contact: int, region_only: bool = False) -> str:
    return f"auto:one-to-one,min_frac={min_frac:g},min_contact={min_contact}" + (",region_only" if region_only else "")


def run_auto_decisions(work, out=None, *, min_frac: float = 0.5, min_contact: int = 50,
                       sample: int = 0, seed: int = 0, sample_out=None, decided_at: str | None = None,
                       region_only: bool = False) -> dict:
    """Write decisions.csv rows ("yes") for auto-accepted candidates and,
    with ``sample``, a reproducible random sample of them for spot checks."""
    work = Path(work)
    if not 0 <= min_frac <= 1:
        raise StitchingError("--min-frac must be between 0 and 1")
    if min_contact < 0 or sample < 0:
        raise StitchingError("--min-contact and --sample must not be negative")
    if region_only:
        require_in_region(work)
    candidates = read_csv(work / "candidates.csv")
    accepted = auto_accept(candidates, min_frac=min_frac, min_contact=min_contact, region_only=region_only)
    reviewer = auto_rule_name(min_frac, min_contact, region_only)
    decided_at = decided_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = Path(out) if out else work / "decisions_auto.csv"
    write_csv(out, DECISION_FIELDS, [
        {**{f: candidates[i][f] for f in DECISION_FIELDS[:7]}, "decision": "yes", "reviewer": reviewer,
         "decided_at": decided_at} for i in accepted])
    result = {"candidates": len(candidates), "accepted": len(accepted), "out": out, "reviewer": reviewer,
              "sample": 0, "sample_out": None}
    if sample:
        picked = sorted(random.Random(seed).sample(accepted, min(sample, len(accepted))))
        sample_out = Path(sample_out) if sample_out else work / f"auto_sample_{len(picked)}_seed{seed}.csv"
        write_csv(sample_out, AUTO_SAMPLE_FIELDS, [{f: candidates[i][f] for f in AUTO_SAMPLE_FIELDS} for i in picked])
        result.update(sample=len(picked), sample_out=sample_out)
    return result
