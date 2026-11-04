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
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
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
    _check_label_dtype(volume.dtype, where)
    nz, ny, nx = volume.shape
    block = max(1, Z_BLOCK_BYTES // max(1, ny * nx * np.dtype(volume.dtype).itemsize))
    counts: dict[int, int] = {}
    for z0 in range(0, nz, block):
        data = np.asarray(volume[z0 : z0 + block])
        if data.size and np.issubdtype(data.dtype, np.signedinteger) and data.min() < 0:
            raise StitchingError(f"{where}: negative label values")
        ids, n = np.unique(data, return_counts=True)
        for lid, c in zip(ids.tolist(), n.tolist()):
            if lid:
                counts[lid] = counts.get(lid, 0) + c
    return counts


# --- phase 2: global IDs -----------------------------------------------------

def _file_fingerprint(path) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


def assign_global_ids(inventory: Inventory) -> tuple[list[dict], list[dict]]:
    """Return ``(tile_ids_rows, id_map_rows)``: per-tile offsets, then one row per
    (tile, nonzero local label). See docs/engineering/stitching.md, phase 2."""
    require_stitchable(inventory)
    tile_rows, id_rows = [], []
    offset = 0
    for tile in inventory.tiles:
        with open_tile(tile.path) as volume:
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
        })
        for lid in sorted(counts):
            id_rows.append({"tile": tile.name, "local_id": lid, "global_id": offset + lid,
                            "voxel_count": counts[lid]})
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


def seam_candidates(inventory: Inventory, tile_ids: dict[str, dict]) -> list[dict]:
    """Candidate merges for every seam, sorted by seam, then contact descending."""
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
        found.sort(key=lambda r: (-r["contact_voxels"], r["label_a"], r["label_b"]))
        rows += found
    return rows


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


def run_ids(work) -> tuple[list[dict], list[dict]]:
    work = Path(work)
    inventory = build_inventory(load_tiles_csv(work))
    tile_rows, id_rows = assign_global_ids(inventory)
    write_csv(work / "tile_ids.csv", TILE_IDS_FIELDS, tile_rows)
    write_csv(work / "id_map.csv", ID_MAP_FIELDS, id_rows)
    return tile_rows, id_rows


def run_candidates(work) -> list[dict]:
    work = Path(work)
    inventory = build_inventory(load_tiles_csv(work))
    require_stitchable(inventory)
    check_seams_csv(work, inventory)
    rows = seam_candidates(inventory, load_tile_ids(work, inventory.tiles))
    write_csv(work / "candidates.csv", CANDIDATES_FIELDS, rows)
    return rows
