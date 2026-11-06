"""Stitching phases 1-5 on synthetic tiles built in temporary directories.

The main fixture is a 2 x 2 x 2 grid of 8 x 32 x 32 tiles (region 16 x 64 x 64)
with hand-placed objects; every expected row below was worked out by hand from
the boxes in ``GRID_OBJECTS``. Raw image tiles for phase 4 hold ``em_value``.
"""

import csv
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock, skipUnless

import numpy as np
import tifffile
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase
from PIL import Image

from annotation import stitching, stitching_measure, stitching_review
from annotation.measurements import measure_label_volume
from annotation.stitching import StitchingError, Tile, parse_tile_name
from annotation.visualization import slice_io

try:
    import h5py
except ImportError:  # pragma: no cover - optional reader
    h5py = None

T0, T1, T2, T3 = "0-8_0-32_0-32_im", "0-8_0-32_32-64_im", "0-8_32-64_0-32_im", "0-8_32-64_32-64_im"
T4, T5, T6, T7 = "8-16_0-32_0-32_im", "8-16_0-32_32-64_im", "8-16_32-64_0-32_im", "8-16_32-64_32-64_im"
GRID_TILES = (T0, T1, T2, T3, T4, T5, T6, T7)

# (name, (z0, z1, y0, y1, x0, x1) in region coordinates, {tile: local id}).
# Local IDs restart per tile so they collide across tiles on purpose.
GRID_OBJECTS = (
    # A: crosses only the Z seam T0 -> T4.
    ("A", (5, 11, 4, 8, 4, 8), {T0: 1, T4: 1}),
    # B: crosses the Y seam T0 -> T2, shifted 2 voxels in x across the seam.
    ("B0", (2, 5, 28, 32, 10, 14), {T0: 2}),
    ("B2", (2, 5, 32, 36, 12, 16), {T2: 1}),
    # C: spans three tiles: X seam T0 -> T1, then Z seam T1 -> T5.
    ("C1", (1, 4, 16, 20, 26, 40), {T0: 3, T1: 1}),
    ("C2", (1, 12, 16, 20, 36, 40), {T1: 1, T5: 70000}),
    # D: touches only the outer boundary (z=0, y=64, x=64).
    ("D", (0, 3, 60, 64, 60, 64), {T3: 1}),
    # G on T2 meets two labels H1/H2 across the Z seam T2 -> T6.
    ("G", (4, 8, 40, 48, 4, 12), {T2: 2}),
    ("H1", (8, 10, 40, 48, 4, 9), {T6: 1}),
    ("H2", (8, 10, 40, 48, 9, 14), {T6: 2}),
    # E ends at x=30, one voxel short of the X seam at 32 (T6's last plane is x=31);
    # F starts on T7's first plane right across from it. Not a candidate.
    ("E", (9, 12, 40, 44, 25, 31), {T6: 3}),
    ("F", (9, 12, 40, 44, 32, 36), {T7: 1}),
)

# T5 needs a local ID above uint16.
GRID_DTYPES = {T5: np.uint32}

EXPECTED_TILES = [
    # tile, z0, z1, y0, y1, x0, x1, grid_z, grid_y, grid_x
    (T0, 0, 8, 0, 32, 0, 32, 0, 0, 0),
    (T1, 0, 8, 0, 32, 32, 64, 0, 0, 1),
    (T2, 0, 8, 32, 64, 0, 32, 0, 1, 0),
    (T3, 0, 8, 32, 64, 32, 64, 0, 1, 1),
    (T4, 8, 16, 0, 32, 0, 32, 1, 0, 0),
    (T5, 8, 16, 0, 32, 32, 64, 1, 0, 1),
    (T6, 8, 16, 32, 64, 0, 32, 1, 1, 0),
    (T7, 8, 16, 32, 64, 32, 64, 1, 1, 1),
]

EXPECTED_SEAMS = [
    # seam, axis, tile_a, tile_b, position, z0, z1, y0, y1, x0, x1, face_voxels
    (0, "z", T0, T4, 8, 8, 8, 0, 32, 0, 32, 1024),
    (1, "y", T0, T2, 32, 0, 8, 32, 32, 0, 32, 256),
    (2, "x", T0, T1, 32, 0, 8, 0, 32, 32, 32, 256),
    (3, "z", T1, T5, 8, 8, 8, 0, 32, 32, 64, 1024),
    (4, "y", T1, T3, 32, 0, 8, 32, 32, 32, 64, 256),
    (5, "z", T2, T6, 8, 8, 8, 32, 64, 0, 32, 1024),
    (6, "x", T2, T3, 32, 0, 8, 32, 64, 32, 32, 256),
    (7, "z", T3, T7, 8, 8, 8, 32, 64, 32, 64, 1024),
    (8, "y", T4, T6, 32, 8, 16, 32, 32, 0, 32, 256),
    (9, "x", T4, T5, 32, 8, 16, 0, 32, 32, 32, 256),
    (10, "y", T5, T7, 32, 8, 16, 32, 32, 32, 64, 256),
    (11, "x", T6, T7, 32, 8, 16, 32, 64, 32, 32, 256),
]

EXPECTED_TILE_IDS = [
    # tile, id_offset, max_local_id, label_count, labelled_voxels, dtype
    (T0, 0, 3, 3, 168, "uint16"),
    (T1, 3, 1, 1, 160, "uint16"),
    (T2, 4, 2, 2, 304, "uint16"),
    (T3, 6, 1, 1, 48, "uint16"),
    (T4, 7, 1, 1, 48, "uint16"),
    (T5, 8, 70000, 1, 64, "uint32"),
    (T6, 70008, 3, 3, 232, "uint16"),
    (T7, 70011, 1, 1, 48, "uint16"),
]

EXPECTED_ID_MAP = [
    # tile, local_id, global_id, voxel_count
    (T0, 1, 1, 48),        # A
    (T0, 2, 2, 48),        # B
    (T0, 3, 3, 72),        # C
    (T1, 1, 4, 160),       # C (both parts)
    (T2, 1, 5, 48),        # B
    (T2, 2, 6, 256),       # G
    (T3, 1, 7, 48),        # D
    (T4, 1, 8, 48),        # A
    (T5, 70000, 70008, 64),  # C
    (T6, 1, 70009, 80),    # H1
    (T6, 2, 70010, 80),    # H2
    (T6, 3, 70011, 72),    # E
    (T7, 1, 70012, 48),    # F
]

EXPECTED_CANDIDATES = [
    # seam, axis, tile_a, label_a, tile_b, label_b, global_a, global_b,
    # contact_voxels, area_a, area_b, frac_a, frac_b
    (0, "z", T0, 1, T4, 1, 1, 8, 16, 16, 16, "1.000000", "1.000000"),           # A
    (1, "y", T0, 2, T2, 1, 2, 5, 6, 12, 12, "0.500000", "0.500000"),            # B
    (2, "x", T0, 3, T1, 1, 3, 4, 12, 12, 12, "1.000000", "1.000000"),           # C
    (3, "z", T1, 1, T5, 70000, 4, 70008, 16, 16, 16, "1.000000", "1.000000"),   # C
    (5, "z", T2, 2, T6, 1, 6, 70009, 40, 64, 40, "0.625000", "1.000000"),       # G-H1
    (5, "z", T2, 2, T6, 2, 6, 70010, 24, 64, 40, "0.375000", "0.600000"),       # G-H2
]


def _write_tiff(path, array, **kwargs):
    tifffile.imwrite(str(path), array, photometric="minisblack", **kwargs)


def build_grid(directory: Path, *, suffix=".tif", **write_kwargs) -> None:
    """Write the 2 x 2 x 2 grid tiles described by ``GRID_OBJECTS``."""
    write_tiles(directory, GRID_TILES, GRID_OBJECTS, GRID_DTYPES, suffix=suffix, **write_kwargs)


def write_tiles(directory: Path, names, objects, dtypes=None, *, suffix=".tif", **write_kwargs) -> None:
    """Write label tiles ``names`` (shapes from their boxes), painting each
    ``(name, region box, {tile: local id})`` object into the tiles it reaches."""
    arrays = {}
    for name in names:
        _, box = parse_tile_name(name)
        shape = tuple(box[2 * a + 1] - box[2 * a] for a in range(3))
        arrays[name] = (box, np.zeros(shape, dtype=(dtypes or {}).get(name, np.uint16)))
    for obj, obox, local_ids in objects:
        for name, (box, array) in arrays.items():
            spans = [(max(obox[2 * a], box[2 * a]), min(obox[2 * a + 1], box[2 * a + 1])) for a in range(3)]
            if any(hi <= lo for lo, hi in spans):
                continue
            if name not in local_ids:
                raise AssertionError(f"fixture: object {obj} reaches {name} without a local id")
            key = tuple(slice(lo - box[2 * a], hi - box[2 * a]) for a, (lo, hi) in enumerate(spans))
            array[key] = local_ids[name]
    for name, (_, array) in arrays.items():
        _write_tiff(directory / f"{name}{suffix}", array, **write_kwargs)


def read_rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def as_tuples(rows, fields):
    return [tuple(int(r[f]) if r[f].lstrip("-").isdigit() else r[f] for f in fields) for r in rows]


class TempDirMixin:
    def make_dir(self, prefix="mito-stitch-"):
        tmp = tempfile.TemporaryDirectory(prefix=prefix)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(slice_io.clear_caches)
        return Path(tmp.name)

    def call(self, name, *args):
        err = io.StringIO()
        call_command(name, *args, stdout=io.StringIO(), stderr=err)
        return err.getvalue()


class TileNameTests(SimpleTestCase):
    def test_dataset_name(self):
        self.assertEqual(
            parse_tile_name("96-352_8192-10240_19456-21504_im"),
            ("96-352_8192-10240_19456-21504_im", (96, 352, 8192, 10240, 19456, 21504)),
        )

    def test_extensions_and_directories_are_not_part_of_the_name(self):
        for raw in ("0-8_0-32_0-32_im.tif", "0-8_0-32_0-32_im.TIFF", "/a/b/0-8_0-32_0-32_im.h5",
                    "rel/0-8_0-32_0-32_im.hdf5", "0-8_0-32_0-32_im.nii.gz", "0-8_0-32_0-32_im.nii",
                    "0-8_0-32_0-32_im.npy"):
            with self.subTest(raw=raw):
                self.assertEqual(parse_tile_name(raw), ("0-8_0-32_0-32_im", (0, 8, 0, 32, 0, 32)))

    def test_suffix_is_optional_and_free_form(self):
        self.assertEqual(parse_tile_name("0-8_0-32_0-32"), ("0-8_0-32_0-32", (0, 8, 0, 32, 0, 32)))
        self.assertEqual(parse_tile_name("0-1_2-3_4-5_6-7")[1], (0, 1, 2, 3, 4, 5))
        self.assertEqual(parse_tile_name("0-1_2-3_4-5_seg_v2.tif")[0], "0-1_2-3_4-5_seg_v2")

    def test_leading_zeros(self):
        self.assertEqual(parse_tile_name("0096-0352_00-10_0-10")[1], (96, 352, 0, 10, 0, 10))

    def test_large_coordinates_are_exact(self):
        self.assertEqual(parse_tile_name("0-1_0-1_9007199254740993-9007199254740995")[1][4:],
                         (9007199254740993, 9007199254740995))

    def test_rejected_names(self):
        for raw in ("im_0-8_0-32_0-32",     # box must come first
                    "0-8_0-32",              # missing an axis
                    "0-8_0-32_0-32im",       # suffix needs an underscore
                    "8-8_0-32_0-32",         # empty z range
                    "10-8_0-32_0-32",        # reversed z range
                    "0-8_0-32_32-0",         # reversed x range
                    "a-8_0-32_0-32",         # non-numeric
                    "0-8-0-32-0-32",         # wrong separators
                    "-1-8_0-32_0-32",        # negative
                    ""):
            with self.subTest(raw=raw), self.assertRaises(StitchingError):
                parse_tile_name(raw)


class GridStitchingTests(TempDirMixin, SimpleTestCase):
    def setUp(self):
        self.tiles_dir = self.make_dir()
        build_grid(self.tiles_dir)
        (self.tiles_dir / "readme.txt").write_text("not a tile")
        self.work = self.make_dir() / "work"

    def run_all(self):
        self.call("stitch_inventory", "--tiles", str(self.tiles_dir), "--work", str(self.work))
        self.call("stitch_ids", "--work", str(self.work))
        self.call("stitch_candidates", "--work", str(self.work))

    def test_inventory(self):
        err = self.call("stitch_inventory", "--tiles", str(self.tiles_dir), "--work", str(self.work))
        self.assertIn("8 tile(s), 12 seam(s)", err)
        self.assertIn("no issues", err)

        tiles = read_rows(self.work / "tiles.csv")
        self.assertEqual(
            as_tuples(tiles, ("tile", "z0", "z1", "y0", "y1", "x0", "x1", "grid_z", "grid_y", "grid_x")),
            EXPECTED_TILES,
        )
        for row in tiles:
            self.assertEqual(row["path"], str((self.tiles_dir / f"{row['tile']}.tif").resolve()))
            self.assertEqual((row["shape_zyx"], row["shape_ok"]), ("8x32x32", "true"))

        seams = read_rows(self.work / "seams.csv")
        self.assertEqual(
            as_tuples(seams, ("seam", "axis", "tile_a", "tile_b", "position",
                              "z0", "z1", "y0", "y1", "x0", "x1", "face_voxels")),
            EXPECTED_SEAMS,
        )
        self.assertEqual([r["direction"] for r in seams], ["+" + r["axis"] for r in seams])
        self.assertEqual(read_rows(self.work / "issues.csv"), [])

    def test_list_file_input_matches_directory_input(self):
        listing = self.tiles_dir / "tiles.txt"
        listing.write_text("# nag-style list\n\n" + "\n".join(f"{n}.tif" for n in reversed(GRID_TILES)) + "\n")
        self.call("stitch_inventory", "--tiles", str(listing), "--work", str(self.work))
        self.assertEqual(
            as_tuples(read_rows(self.work / "tiles.csv"), ("tile", "z0", "z1", "y0", "y1", "x0", "x1",
                                                          "grid_z", "grid_y", "grid_x")),
            EXPECTED_TILES,
        )
        self.assertEqual(len(read_rows(self.work / "seams.csv")), 12)

    def test_global_ids(self):
        self.call("stitch_inventory", "--tiles", str(self.tiles_dir), "--work", str(self.work))
        err = self.call("stitch_ids", "--work", str(self.work))
        self.assertIn("13 label(s) in 8 tile(s); largest global id 70012", err)
        self.assertEqual(
            as_tuples(read_rows(self.work / "tile_ids.csv"),
                      ("tile", "id_offset", "max_local_id", "label_count", "labelled_voxels", "dtype")),
            EXPECTED_TILE_IDS,
        )
        id_map = as_tuples(read_rows(self.work / "id_map.csv"), ("tile", "local_id", "global_id", "voxel_count"))
        self.assertEqual(id_map, EXPECTED_ID_MAP)
        self.assertEqual(len({row[2] for row in id_map}), len(id_map))
        self.assertGreater(max(row[2] for row in id_map), np.iinfo(np.uint16).max)

    def test_ids_are_deterministic(self):
        self.run_all()
        first = {n: (self.work / n).read_text() for n in ("tile_ids.csv", "id_map.csv", "candidates.csv")}
        self.run_all()
        for name, text in first.items():
            self.assertEqual((self.work / name).read_text(), text, name)

    def test_candidates(self):
        self.run_all()
        rows = read_rows(self.work / "candidates.csv")
        self.assertEqual(list(rows[0].keys()), list(stitching.CANDIDATES_FIELDS))
        self.assertEqual(
            as_tuples(rows, stitching.CANDIDATES_FIELDS),
            EXPECTED_CANDIDATES,
        )
        pairs = {(r["tile_a"], r["label_a"], r["tile_b"], r["label_b"]) for r in rows}
        # D touches only the outer boundary; E stops one voxel short of seam 11.
        self.assertFalse(any(T3 in (p[0], p[2]) for p in pairs))
        self.assertFalse(any(r["seam"] == "11" for r in rows))

    def test_candidates_read_only_facing_planes(self):
        keys = []
        real_open = stitching.open_label_volume_readonly

        class Recorder:
            def __init__(self, volume):
                self.volume, self.shape, self.dtype = volume, volume.shape, volume.dtype

            def __getitem__(self, key):
                keys.append(key)
                return self.volume[key]

        self.call("stitch_inventory", "--tiles", str(self.tiles_dir), "--work", str(self.work))
        self.call("stitch_ids", "--work", str(self.work))
        with mock.patch.object(stitching, "open_label_volume_readonly", lambda p: Recorder(real_open(p))):
            self.call("stitch_candidates", "--work", str(self.work))
        self.assertEqual(len(keys), 24)  # two planes per seam
        for key in keys:
            self.assertEqual(sum(isinstance(k, int) for k in key), 1, key)

    def test_candidates_refuse_a_tile_changed_after_ids(self):
        self.call("stitch_inventory", "--tiles", str(self.tiles_dir), "--work", str(self.work))
        self.call("stitch_ids", "--work", str(self.work))
        path = self.tiles_dir / f"{T4}.tif"
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        with self.assertRaisesMessage(CommandError, "changed since stitch_ids"):
            self.call("stitch_candidates", "--work", str(self.work))

    def test_candidates_refuse_stale_seams(self):
        self.run_all()
        seams = self.work / "seams.csv"
        seams.write_text("\n".join(seams.read_text().splitlines()[:-1]) + "\n")
        with self.assertRaisesMessage(CommandError, "seams.csv does not match"):
            self.call("stitch_candidates", "--work", str(self.work))

    def test_later_phases_need_earlier_outputs(self):
        with self.assertRaisesMessage(CommandError, "tiles.csv not found"):
            self.call("stitch_ids", "--work", str(self.work))
        self.call("stitch_inventory", "--tiles", str(self.tiles_dir), "--work", str(self.work))
        with self.assertRaisesMessage(CommandError, "tile_ids.csv not found"):
            self.call("stitch_candidates", "--work", str(self.work))


class CompressedTiffTests(TempDirMixin, SimpleTestCase):
    """A TIFF that cannot be memory-mapped is read page by page, never whole."""

    def test_grid_stitches_identically(self):
        tiles_dir = self.make_dir()
        build_grid(tiles_dir, compression="zlib")
        self.assertFalse(stitching._tiff_is_memmappable(tiles_dir / f"{T0}.tif"))
        work = self.make_dir() / "work"

        def refuse(*args, **kwargs):
            raise AssertionError("whole-file read")

        with mock.patch.object(stitching, "open_label_volume_readonly", refuse), \
                mock.patch.object(tifffile, "imread", refuse), \
                mock.patch.object(tifffile.TiffFile, "asarray", refuse):
            self.call("stitch_inventory", "--tiles", str(tiles_dir), "--work", str(work))
            self.call("stitch_ids", "--work", str(work))
            self.call("stitch_candidates", "--work", str(work))
        self.assertEqual(
            as_tuples(read_rows(work / "id_map.csv"), ("tile", "local_id", "global_id", "voxel_count")),
            EXPECTED_ID_MAP,
        )
        self.assertEqual(as_tuples(read_rows(work / "candidates.csv"), stitching.CANDIDATES_FIELDS),
                         EXPECTED_CANDIDATES)

    def test_paged_planes_match_the_array(self):
        root = self.make_dir()
        array = np.arange(4 * 5 * 6, dtype=np.uint32).reshape(4, 5, 6)
        _write_tiff(root / "t.tif", array, compression="zlib")
        volume = stitching.PagedTiff(root / "t.tif")
        self.addCleanup(volume.close)
        self.assertEqual(volume.shape, (4, 5, 6))
        np.testing.assert_array_equal(volume[3], array[3])
        np.testing.assert_array_equal(volume[:, 4, :], array[:, 4, :])
        np.testing.assert_array_equal(volume[:, 1:3, 5], array[:, 1:3, 5])
        np.testing.assert_array_equal(volume[1:3], array[1:3])


@skipUnless(h5py is not None, "h5py is not installed")
class Hdf5TileTests(TempDirMixin, SimpleTestCase):
    def test_face_planes_from_hdf5(self):
        root = self.make_dir()
        array = np.arange(4 * 5 * 6, dtype=np.uint64).reshape(4, 5, 6)
        path = root / "0-4_0-5_0-6_im.h5"
        with h5py.File(path, "w") as fh:
            fh.create_dataset("main", data=array, chunks=(1, 5, 6))
        tile = Tile(*parse_tile_name(path.name), str(path))
        seam_x = stitching.Seam(0, 2, tile.name, "other", 6, (0, 4, 1, 4, 6, 6))
        seam_y = stitching.Seam(1, 1, "other", tile.name, 0, (1, 3, 0, 0, 0, 6))
        with stitching.open_tile(path) as volume:
            self.assertEqual(tuple(volume.shape), (4, 5, 6))
            np.testing.assert_array_equal(stitching.face_plane(volume, tile, seam_x, side="a"), array[:, 1:4, 5])
            np.testing.assert_array_equal(stitching.face_plane(volume, tile, seam_y, side="b"), array[1:3, 0, :])


class InventoryReportTests(TempDirMixin, SimpleTestCase):
    def inventory_from_names(self, names):
        listing = self.make_dir() / "tiles.txt"
        listing.write_text("\n".join(names) + "\n")
        work = listing.parent / "work"
        self.call("stitch_inventory", "--tiles", str(listing), "--work", str(work))
        return work

    def issues(self, work):
        return [(r["kind"], r["axis"], r["tile_a"], r["tile_b"],
                 tuple(int(r[k]) for k in ("z0", "z1", "y0", "y1", "x0", "x1")) if r["z0"] else None,
                 r["detail"])
                for r in read_rows(work / "issues.csv") if r["kind"] != "no_file"]

    def test_gap_and_missing_positions(self):
        work = self.inventory_from_names([
            "0-4_0-10_0-10_im", "0-4_0-10_10-20_im", "0-4_0-10_25-35_im", "0-4_10-20_0-10_im",
        ])
        self.assertEqual(self.issues(work), [
            ("gap", "x", "", "", None, "x 20-25 has no tiles"),
            ("missing_position", "", "", "", (0, 4, 10, 20, 10, 20), "grid position 0-4_10-20_10-20 has no tile"),
            ("missing_position", "", "", "", (0, 4, 10, 20, 25, 35), "grid position 0-4_10-20_25-35 has no tile"),
        ])
        no_file = [r["tile_a"] for r in read_rows(work / "issues.csv") if r["kind"] == "no_file"]
        self.assertEqual(len(no_file), 4)
        seams = as_tuples(read_rows(work / "seams.csv"), ("seam", "axis", "tile_a", "tile_b", "position"))
        self.assertEqual(seams, [
            (0, "y", "0-4_0-10_0-10_im", "0-4_10-20_0-10_im", 10),
            (1, "x", "0-4_0-10_0-10_im", "0-4_0-10_10-20_im", 10),
        ])
        tiles = as_tuples(read_rows(work / "tiles.csv"), ("tile", "grid_z", "grid_y", "grid_x", "path", "shape_ok"))
        self.assertEqual(tiles[2], ("0-4_0-10_25-35_im", 0, 0, 2, "", ""))

    def test_overlap_duplicate_and_irregular_axis(self):
        work = self.inventory_from_names([
            "0-4_0-10_0-10_im", "0-4_0-10_0-10_seg", "0-4_0-10_8-18_im",
        ])
        self.assertEqual(self.issues(work), [
            ("duplicate", "", "0-4_0-10_0-10_im", "0-4_0-10_0-10_seg", (0, 4, 0, 10, 0, 10),
             "0-4_0-10_0-10_im and 0-4_0-10_0-10_seg"),
            ("overlap", "", "0-4_0-10_0-10_im", "0-4_0-10_8-18_im", (0, 4, 0, 10, 8, 10), "80 voxels"),
            ("overlap", "", "0-4_0-10_0-10_seg", "0-4_0-10_8-18_im", (0, 4, 0, 10, 8, 10), "80 voxels"),
            ("irregular_axis", "x", "", "", None, "tile intervals on this axis overlap: 0-10 8-18"),
        ])
        self.assertEqual(read_rows(work / "seams.csv"), [])
        with self.assertRaisesMessage(CommandError, "duplicate"):
            self.call("stitch_ids", "--work", str(work))

    def test_partial_face_is_the_intersection(self):
        work = self.inventory_from_names(["0-4_0-10_0-10_im", "4-8_0-5_2-10_im"])
        seams = as_tuples(read_rows(work / "seams.csv"), stitching.SEAMS_FIELDS)
        self.assertEqual(seams, [(0, "z", "+z", "0-4_0-10_0-10_im", "4-8_0-5_2-10_im", 4, 4, 4, 0, 5, 2, 10, 40)])

    def test_tiles_touching_only_at_an_edge_share_no_face(self):
        work = self.inventory_from_names(["0-4_0-10_0-10_im", "0-4_10-20_10-20_im", "4-8_10-20_0-10_im"])
        self.assertEqual(read_rows(work / "seams.csv"), [])

    def test_list_file_line_that_does_not_parse_is_an_error(self):
        listing = self.make_dir() / "tiles.txt"
        listing.write_text("0-4_0-10_0-10_im\nnot-a-tile\n")
        with self.assertRaisesMessage(CommandError, "tiles.txt:2"):
            self.call("stitch_inventory", "--tiles", str(listing), "--work", str(listing.parent / "w"))

    def test_directory_reports_unparsed_label_files_and_shape_mismatch(self):
        root = self.make_dir()
        _write_tiff(root / "0-4_0-10_0-10_im.tif", np.zeros((4, 10, 10), np.uint16))
        _write_tiff(root / "4-8_0-10_0-10_im.tif", np.zeros((4, 10, 9), np.uint16))
        _write_tiff(root / "overview.tif", np.zeros((1, 2, 2), np.uint16))
        (root / "notes.txt").write_text("ignored")
        work = root / "work"
        err = self.call("stitch_inventory", "--tiles", str(root), "--work", str(work))
        self.assertIn("shape_mismatch: 1, unparsed_name: 1", err)
        kinds = {r["kind"]: r for r in read_rows(work / "issues.csv")}
        self.assertEqual(kinds["shape_mismatch"]["detail"], "file shape 4x10x9 but name implies 4x10x10")
        self.assertIn("overview.tif", kinds["unparsed_name"]["detail"])
        with self.assertRaisesMessage(CommandError, "shape_mismatch"):
            self.call("stitch_ids", "--work", str(work))

    def test_names_only_inventory_cannot_assign_ids(self):
        work = self.inventory_from_names(["0-4_0-10_0-10_im"])
        with self.assertRaisesMessage(CommandError, "no_file"):
            self.call("stitch_ids", "--work", str(work))


class GlobalIdRangeTests(TempDirMixin, SimpleTestCase):
    def two_tiles(self, first, second):
        root = self.make_dir()
        for name, lid in (("0-1_0-2_0-2_im", first), ("1-2_0-2_0-2_im", second)):
            array = np.zeros((1, 2, 2), np.uint64)
            array[0, 1, 1] = lid
            np.save(root / f"{name}.npy", array)
        work = root / "work"
        self.call("stitch_inventory", "--tiles", str(root), "--work", str(work))
        return work

    def test_ids_beyond_float_precision_are_exact(self):
        work = self.two_tiles(2**62, 2**62 + 1)
        self.call("stitch_ids", "--work", str(work))
        self.assertEqual(
            as_tuples(read_rows(work / "id_map.csv"), ("tile", "local_id", "global_id")),
            [("0-1_0-2_0-2_im", 2**62, 2**62), ("1-2_0-2_0-2_im", 2**62 + 1, 2**63 + 1)],
        )
        self.call("stitch_candidates", "--work", str(work))
        self.assertEqual(
            as_tuples(read_rows(work / "candidates.csv"), ("label_a", "label_b", "global_a", "global_b")),
            [(2**62, 2**62 + 1, 2**62, 2**63 + 1)],
        )

    def test_overflowing_uint64_is_refused(self):
        work = self.two_tiles(2**63 + 1, 2**63)
        with self.assertRaisesMessage(CommandError, "exceeds uint64"):
            self.call("stitch_ids", "--work", str(work))

    def test_non_integer_and_negative_labels_are_refused(self):
        for array, message in ((np.zeros((1, 2, 2), np.float32), "must be integers"),
                               (np.zeros((1, 2, 2), np.bool_), "must be integers"),
                               (np.full((1, 2, 2), -1, np.int32), "negative")):
            with self.subTest(dtype=array.dtype), self.assertRaisesMessage(StitchingError, message):
                stitching.count_labels(array)


# --- phase 4: review page ------------------------------------------------------

def em_value(z, y, x):
    """Raw image intensity at region (z, y, x); distinct enough to check stitching."""
    return (np.asarray(z) * 37 + np.asarray(y) * 5 + np.asarray(x) * 3) % 256


def build_images(directory: Path, *, skip=(), suffix="_raw.tif") -> None:
    """uint8 raw image tiles for the grid, named ``<box><suffix>``."""
    for name in GRID_TILES:
        if name in skip:
            continue
        _, (z0, z1, y0, y1, x0, x1) = parse_tile_name(name)
        zz, yy, xx = np.meshgrid(np.arange(z0, z1), np.arange(y0, y1), np.arange(x0, x1), indexing="ij")
        _write_tiff(directory / f"{name[:-len('_im')]}{suffix}", em_value(zz, yy, xx).astype(np.uint8))


def png(path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def perimeter(shape, rows, cols) -> np.ndarray:
    """Mask of the border of the box rows[0]..rows[1]-1 x cols[0]..cols[1]-1."""
    mask = np.zeros(shape, bool)
    mask[rows[0]:rows[1], cols[0]:cols[1]] = True
    inner = np.zeros(shape, bool)
    inner[rows[0] + 1:rows[1] - 1, cols[0] + 1:cols[1] - 1] = True
    return mask & ~inner


class StitchedGridMixin(TempDirMixin):
    """The grid after phases 1-3, plus raw image tiles."""

    def setUp(self):
        self.tiles_dir = self.make_dir()
        build_grid(self.tiles_dir)
        self.work = self.make_dir() / "work"
        self.call("stitch_inventory", "--tiles", str(self.tiles_dir), "--work", str(self.work))
        self.call("stitch_ids", "--work", str(self.work))
        self.call("stitch_candidates", "--work", str(self.work))
        self.candidates = read_rows(self.work / "candidates.csv")

    def images(self, **kwargs) -> Path:
        directory = self.make_dir()
        build_images(directory, **kwargs)
        return directory


class CardGeometryTests(SimpleTestCase):
    def test_z_seam_centres_on_the_contact(self):
        seam = stitching.Seam(0, 0, "a", "b", 8, (8, 8, 0, 32, 0, 32))
        positions = np.array([(8, y, x) for y in (10, 11) for x in (20, 21, 22, 23)])
        view = stitching_review.card_view(seam, positions, 16)
        self.assertEqual(view.slices, (("a", 7), ("b", 8)))
        self.assertEqual(view.window, (3, 19, 14, 30))   # centre (11, 22): 10.5 and 21.5 round up
        self.assertIsNone(view.seam_px)

    def test_x_seam_puts_the_seam_in_the_middle(self):
        seam = stitching.Seam(0, 2, "a", "b", 32, (0, 8, 0, 32, 32, 32))
        positions = np.array([(2, 5, 32), (3, 9, 32), (3, 12, 32), (6, 30, 32)])  # mean z 3.5 -> nearest contact z 3
        view = stitching_review.card_view(seam, positions, 16)
        self.assertEqual(view.slices, ((None, 3),))
        self.assertEqual(view.window, (3, 19, 24, 40))   # y centre: mean of 9, 12 at z=3 -> 11 (10.5 up)
        self.assertEqual(view.seam_px, 8)

    def test_y_seam_and_tie_to_the_smaller_z(self):
        seam = stitching.Seam(0, 1, "a", "b", 32, (0, 8, 32, 32, 0, 32))
        positions = np.array([(2, 32, 4), (4, 32, 7)])   # mean z 3: z 2 and 4 tie -> 2
        view = stitching_review.card_view(seam, positions, 10)
        self.assertEqual(view.slices, ((None, 2),))
        self.assertEqual(view.window, (27, 37, -1, 9))
        self.assertEqual(view.seam_px, 5)

    def test_no_contact_is_an_error(self):
        seam = stitching.Seam(3, 0, "a", "b", 8, (8, 8, 0, 4, 0, 4))
        with self.assertRaisesMessage(StitchingError, "no contact"):
            stitching_review.card_view(seam, np.empty((0, 3), np.int64), 16)


class ReviewPageTests(StitchedGridMixin, SimpleTestCase):
    def build(self, images=None, **kwargs):
        out = kwargs.pop("out", None) or (self.make_dir() / "review")
        return stitching_review.build_review(self.work, [images or self.images()], out, **kwargs)

    def test_cards_cover_candidates_with_expected_views(self):
        result = self.build(crop=16)
        self.assertEqual([c["key"] for c in result.cards],
                         [f"{r['seam']}:{r['label_a']}:{r['label_b']}" for r in self.candidates])
        views = [(c["view"].slices, c["view"].window, c["view"].seam_px) for c in result.cards]
        self.assertEqual(views, [
            ((("a", 7), ("b", 8)), (-2, 14, -2, 14), None),   # A at the region corner
            (((None, 3),), (24, 40, 5, 21), 8),               # B, Y seam
            (((None, 2),), (10, 26, 24, 40), 8),              # C, X seam
            ((("a", 7), ("b", 8)), (10, 26, 30, 46), None),   # C, Z seam
            ((("a", 7), ("b", 8)), (36, 52, -2, 14), None),   # G-H1
            ((("a", 7), ("b", 8)), (36, 52, 2, 18), None),    # G-H2
        ])

    def test_xy_seam_slice_is_stitched_from_both_tiles(self):
        card = self.build_into(crop=16).cards[1]   # Y seam at y=32: rows 0-7 from T0, 8-15 from T2
        rows, cols = np.mgrid[0:16, 0:16]
        expected = em_value(3, 24 + rows, 5 + cols).astype(np.uint8)
        em = png(self.out_path(card, "em"))
        np.testing.assert_array_equal(em[..., 0], expected)
        np.testing.assert_array_equal(em[..., 0], em[..., 2])

    def out_path(self, card, kind, side=0):
        return self._out / card["images"][side][kind]

    def build_into(self, **kwargs):
        self._out = self.make_dir() / "review"
        return self.build(out=self._out, **kwargs)

    def test_outlines_on_an_xy_seam(self):
        card = self.build_into(crop=16).cards[1]
        em, ov = png(self.out_path(card, "em")), png(self.out_path(card, "ov"))
        a = perimeter((16, 16), (4, 8), (5, 9))     # B in T0: y28-32, x10-14
        b = perimeter((16, 16), (8, 12), (7, 11))   # B in T2: y32-36, x12-16
        np.testing.assert_array_equal(np.all(ov == stitching_review.COLOR_A, axis=2), a)
        np.testing.assert_array_equal(np.all(ov == stitching_review.COLOR_B, axis=2), b)
        np.testing.assert_array_equal(ov[~(a | b)], em[~(a | b)])   # no other labels in view

    def test_outlines_and_faint_labels_on_a_z_seam(self):
        card = self.build_into(crop=16).cards[4]           # G (T2, z=7) vs H1 (T6, z=8)
        ov_a, ov_b = png(self.out_path(card, "ov", 0)), png(self.out_path(card, "ov", 1))
        em_b = png(self.out_path(card, "em", 1))
        np.testing.assert_array_equal(np.all(ov_a == stitching_review.COLOR_A, axis=2),
                                      perimeter((16, 16), (4, 12), (6, 14)))   # G: y40-48, x4-12
        self.assertFalse(np.all(ov_a == stitching_review.COLOR_B, axis=2).any())
        np.testing.assert_array_equal(np.all(ov_b == stitching_review.COLOR_B, axis=2),
                                      perimeter((16, 16), (4, 12), (6, 11)))   # H1: x4-9
        self.assertFalse(np.all(ov_b == stitching_review.COLOR_A, axis=2).any())
        # H2 (x9-14) is another label: its edge next to H1 is blended toward white,
        # its interior is untouched.
        edge = (slice(4, 12), 11)
        np.testing.assert_array_equal(ov_b[edge], (em_b[edge] * 0.6 + 255 * 0.4).astype(np.uint8))
        np.testing.assert_array_equal(ov_b[6, 13], em_b[6, 13])

    def test_crop_past_the_region_corner_is_clipped(self):
        card = self.build_into(crop=16).cards[0]           # window y,x -2..14 at the region corner
        for side, z in ((0, 7), (1, 8)):
            em = png(self.out_path(card, "em", side))
            self.assertTrue(np.all(em[:2] == stitching_review.NO_DATA))
            self.assertTrue(np.all(em[:, :2] == stitching_review.NO_DATA))
            rows, cols = np.mgrid[0:14, 0:14]
            np.testing.assert_array_equal(em[2:, 2:, 0], em_value(z, rows, cols).astype(np.uint8))

    def test_default_crop_is_larger_than_the_tiles(self):
        result = self.build_into()
        for card in result.cards:
            for image in card["images"]:
                for kind in ("em", "ov"):
                    self.assertEqual(png(self._out / image[kind]).shape, (160, 160, 3))
        em = png(self._out / result.cards[2]["images"][0]["em"])   # X seam at y=18: tiles span y 0-32
        self.assertTrue(np.all(em[:62] == stitching_review.NO_DATA))  # window starts at y=-62
        self.assertTrue(np.all(em[94:] == stitching_review.NO_DATA))  # tiles end at y=32 (row 94)

    def test_pairing_by_box_with_unmatched_tiles(self):
        images = self.images(skip=(T6,))
        _write_tiff(images / "16-24_0-32_0-32_raw.tif", np.zeros((8, 32, 32), np.uint8))
        result = self.build_into(images=images, crop=16)
        rows = read_rows(self.work / "image_pairing.csv")
        self.assertEqual([(r["tile"], r["image_tile"], r["status"]) for r in rows], [
            *[(t, t[:-len("_im")] + "_raw", "matched") for t in (T0, T1, T2, T3, T4, T5)],
            (T6, "", "no_image"),
            (T7, T7[:-len("_im")] + "_raw", "matched"),
            ("", "16-24_0-32_0-32_raw", "no_prediction"),
        ])
        card = result.cards[4]
        self.assertEqual(card["no_image"], [T6])
        em_b = png(self.out_path(card, "em", 1))
        self.assertTrue(np.all(em_b[:, 2:] == stitching_review.NO_IMAGE_GRAY))   # T6 starts at col 2 (x=0)
        page = (self._out / "index.html").read_text()
        self.assertIn(f"No raw image for {T6}", page)
        self.assertIn("1 tile(s) have no raw image", page)

    def test_pairing_ignores_suffixes(self):
        root = self.make_dir()
        pred_dir, image_dir = root / "pred", root / "img"
        pred_dir.mkdir()
        image_dir.mkdir()
        for directory, suffix in ((pred_dir, "_im_xy.npy"), (image_dir, "_im.npy")):
            np.save(directory / f"96-100_8192-8196_19456-19460{suffix}", np.zeros((4, 4, 4), np.uint8))
        tiles, _ = stitching.collect_tiles(pred_dir)
        pairing, unused = stitching_review.pair_images(tiles, [image_dir])
        self.assertEqual(pairing["96-100_8192-8196_19456-19460_im_xy"].name, "96-100_8192-8196_19456-19460_im")
        self.assertEqual(unused, [])

    def test_pairing_refuses_ambiguous_or_mismatched_images(self):
        first, second = self.images(), self.images()
        with self.assertRaisesMessage(StitchingError, "two image tiles share box 0-8_0-32_0-32"):
            stitching_review.build_review(self.work, [first, second], self.make_dir() / "r")
        bad = self.images(skip=(T0,))
        _write_tiff(bad / "0-8_0-32_0-32_raw.tif", np.zeros((8, 32, 31), np.uint8))
        with self.assertRaisesMessage(StitchingError, "has shape 8x32x31"):
            stitching_review.build_review(self.work, [bad], self.make_dir() / "r")

    def test_shards_cover_every_candidate_exactly_once(self):
        images = self.images()
        everything = [f"{r['seam']}:{r['label_a']}:{r['label_b']}" for r in self.candidates]
        for n in (1, 2, 4, 6, 9):
            keys = []
            for i in range(1, n + 1):
                result = stitching_review.build_review(self.work, [images], self.make_dir() / "r",
                                                       crop=8, shard=f"{i}/{n}")
                keys += [c["key"] for c in result.cards]
            with self.subTest(n=n):
                self.assertEqual(keys, everything)
        for bad in ("0/3", "4/3", "x/3", "3"):
            with self.subTest(shard=bad), self.assertRaises(StitchingError):
                stitching_review.parse_shard(bad)

    def test_page_is_self_contained(self):
        result = self.build_into(crop=16)
        page = (self._out / "index.html").read_text()
        self.assertNotRegex(page, r"(?i:https?://|<link\b|<script[^>]*\bsrc=|<iframe|@import)|\burl\(")
        refs = re.findall(r'(?:src|href)="([^"]*)"', page)
        self.assertEqual(len(refs), 2 * 2 + 4 * 4)   # X/Y cards: em + ov; Z cards: em + ov per side
        for ref in refs:
            self.assertTrue((self._out / ref).is_file(), ref)
            self.assertFalse(ref.startswith(("/", "..")), ref)
        on_disk = {str(p.relative_to(self._out)) for p in (self._out / "crops").iterdir()}
        self.assertEqual(on_disk, set(refs))
        self.assertEqual(len(re.findall(r'<section class="card"', page)), len(self.candidates))

    def test_embedded_card_data(self):
        self.build_into(crop=16)
        page = (self._out / "index.html").read_text()
        data = json.loads(re.search(r'<script type="application/json" id="review-data">(.*?)</script>',
                                    page, re.S).group(1))
        self.assertEqual(data["export_name"], "decisions.csv")
        self.assertEqual(data["fields"], list(stitching.DECISION_FIELDS))
        self.assertEqual(len(data["cards"]), 6)
        self.assertEqual(data["cards"][3], {
            "id": "c000003", "key": "3:1:70000", "seam": "3", "tile_a": T1, "label_a": "1",
            "tile_b": T5, "label_b": "70000", "global_a": "4", "global_b": "70008"})
        for card in data["cards"]:
            self.assertTrue(all(isinstance(v, str) for v in card.values()))
            self.assertIn(f'id="{card["id"]}"', page)

    def test_only_planes_are_read(self):
        keys = []
        real_open = stitching.open_label_volume_readonly

        class Recorder:
            def __init__(self, volume):
                self.volume, self.shape, self.dtype = volume, volume.shape, volume.dtype

            def __getitem__(self, key):
                keys.append(key)
                return self.volume[key]

        images = self.images()
        with mock.patch.object(stitching, "open_label_volume_readonly", lambda p: Recorder(real_open(p))):
            self.build(images=images, crop=16)
        self.assertTrue(keys)
        for key in keys:
            self.assertEqual(sum(isinstance(k, int) for k in key), 1, key)

    def test_stale_candidates_are_refused(self):
        path = self.work / "candidates.csv"
        text = path.read_text().replace(f",{T0},2,{T2},1,2,5,6,", f",{T0},2,{T2},1,2,5,7,")
        path.write_text(text)
        with self.assertRaisesMessage(StitchingError, "rerun stitch_candidates"):
            self.build(crop=16)

    def test_command_refuses_a_non_empty_folder_without_overwrite(self):
        images = self.images()
        err = self.call("stitch_review", "--work", str(self.work), "--images", str(images), "--crop", "16")
        self.assertIn("8 tile(s) paired, 0 without image, 0 image(s) without prediction", err)
        self.assertIn("6 card(s)", err)
        self.assertTrue((self.work / "review" / "index.html").is_file())
        with self.assertRaisesMessage(CommandError, "--overwrite"):
            self.call("stitch_review", "--work", str(self.work), "--images", str(images))
        self.call("stitch_review", "--work", str(self.work), "--images", str(images), "--overwrite",
                  "--shard", "2/2")
        self.assertTrue((self.work / "review_2of2" / "index.html").is_file())


@skipUnless(shutil.which("node"), "node is not installed")
class ReviewPageScriptTests(StitchedGridMixin, SimpleTestCase):
    """Run the page's script under node with a minimal stand-in for the DOM."""

    HARNESS = r"""
const DATA = __DATA__;
const elements = {};
function makeEl(id) {
  return { id, hidden: false, textContent: "", value: "", checked: false, files: [], attrs: {}, listeners: {},
    addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
    fire(t) { (this.listeners[t] || []).forEach(f => f.call(this, {})); },
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; },
    querySelector() { return this.decision || (this.decision = makeEl(id + "-decision")); },
    blur() {}, remove() {} };
}
function el(id) { return elements[id] || (elements[id] = makeEl(id)); }
const buttons = [];
const docListeners = {};
function reset() {  // a fresh page load; localStorage survives
  for (const k of Object.keys(elements)) delete elements[k];
  for (const k of Object.keys(docListeners)) delete docListeners[k];
  buttons.length = 0;
  ["yes", "no", "unsure"].forEach(d => { const b = el("button-" + d); b.attrs["data-decision"] = d; buttons.push(b); });
  JSON.parse(DATA).cards.forEach(c => { el(c.id).hidden = true; });
}
let blob = null, download = null;
global.document = {
  getElementById: id => id === "review-data" ? { textContent: DATA } : el(id),
  querySelectorAll: () => buttons,
  addEventListener: (t, f) => { docListeners[t] = f; },
  createElement: () => ({ click() { download = this.download; }, remove() {} }),
  body: { appendChild() {} },
};
global.localStorage = { store: {}, getItem(k) { return k in this.store ? this.store[k] : null; },
                        setItem(k, v) { this.store[k] = String(v); } };
global.Blob = class { constructor(parts) { this.text = parts.join(""); } };
URL.createObjectURL = b => { blob = b; return "blob:review"; };
URL.revokeObjectURL = () => {};
global.FileReader = class { readAsText(f) { this.result = f.text; this.onload(); } };
function key(k) { docListeners.keydown({ key: k, target: { tagName: "BODY" }, preventDefault() {} }); }
function visible() { return Object.values(elements).filter(e => /^c\d+$/.test(e.id) && !e.hidden).map(e => e.id); }
const log = [];
function snap(label) { log.push({ label, visible: visible(), progress: el("progress").textContent,
                                  position: el("position").textContent, message: el("message").textContent }); }
function boot() {
  reset();
__SCRIPT__
}
boot();
__SCENARIO__
"""

    SCENARIO = r"""
snap("start");
el("reviewer").value = "rv"; el("reviewer").fire("input");
key("y"); snap("after y");
key("N"); snap("after n");
key("ArrowLeft"); key("u"); snap("after left u");
el("undecided-only").checked = true; el("undecided-only").fire("change"); snap("filter");
buttons[0].fire("click"); snap("click yes");
key("ArrowLeft"); snap("left with filter");
el("export").fire("click");
const exported = blob.text;
el("import").files = [{ text: "seam,tile_a,label_a,tile_b,label_b,global_a,global_b,decision,reviewer,decided_at\n" +
  "5,__T2__,2,__T6__,2,6,70010,no,other,2026-01-01T00:00:00Z\n" +
  "9,x,1,y,1,1,1,yes,other,2026-01-01T00:00:00Z\n" }];
el("import").fire("change"); snap("import");
console.log(JSON.stringify({ log, exported, download, stored: global.localStorage.store }));
"""

    def run_page(self, scenario=None, **values):
        out = self.make_dir() / "review"
        stitching_review.build_review(self.work, [self.images()], out, crop=8)
        page = (out / "index.html").read_text()
        data = re.search(r'<script type="application/json" id="review-data">(.*?)</script>', page, re.S).group(1)
        script = re.search(r"<script>\n(.*?)</script>", page, re.S).group(1)
        harness = (self.HARNESS.replace("__DATA__", json.dumps(data)).replace("__SCRIPT__", script)
                   .replace("__SCENARIO__", scenario or self.SCENARIO)
                   .replace("__T2__", T2).replace("__T6__", T6))
        for name, value in values.items():
            harness = harness.replace(f"__{name}__", json.dumps(value))
        path = out.parent / "harness.js"
        path.write_text(harness)
        check = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        run = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    REVIEWER_SCENARIO = r"""
el("import").files = [{ text: __AUTO__ }];
el("import").fire("change");
const imported = el("message").textContent;
key("ArrowRight");                                   // card 1, not auto-accepted
key("y");                                            // decided before a reviewer name was typed
el("reviewer").value = "late"; el("reviewer").fire("input");
el("export").fire("click");
const exported = blob.text;
boot();                                              // reload the page
console.log(JSON.stringify({ imported, exported, reviewer: el("reviewer").value,
                             progress: el("progress").textContent }));
"""

    def test_reviewer_field_with_auto_decisions(self):
        auto = stitching.run_auto_decisions(self.work, min_contact=12, decided_at="2026-11-05T00:00:00Z")
        result = self.run_page(self.REVIEWER_SCENARIO, AUTO=Path(auto["out"]).read_text())
        self.assertEqual(result["imported"], "Imported 3 decision(s); skipped 0 row(s) not on this page.")
        rule = "auto:one-to-one,min_frac=0.5,min_contact=12"
        rows = list(csv.DictReader(io.StringIO(result["exported"])))
        self.assertEqual([(r["seam"], r["decision"], r["reviewer"]) for r in rows], [
            ("0", "yes", rule), ("1", "yes", "late"), ("2", "yes", rule), ("3", "yes", rule)])
        self.assertEqual(rows[0]["decided_at"], "2026-11-05T00:00:00Z")
        self.assertEqual(result["reviewer"], "late")          # saved with progress
        self.assertEqual(result["progress"], "4 / 6 decided")

    def test_keys_filter_export_and_import(self):
        result = self.run_page()
        steps = {s["label"]: s for s in result["log"]}
        self.assertEqual(steps["start"]["visible"], ["c000000"])
        self.assertEqual(steps["start"]["progress"], "0 / 6 decided")
        self.assertEqual(steps["after y"]["visible"], ["c000001"])
        self.assertEqual(steps["after n"]["position"], "Card 3 of 6")
        self.assertEqual(steps["after left u"]["visible"], ["c000002"])     # u re-decided card 2 and moved on
        self.assertEqual(steps["after left u"]["progress"], "2 / 6 decided")
        self.assertEqual(steps["filter"]["visible"], ["c000002"])           # still undecided
        self.assertEqual(steps["click yes"]["visible"], ["c000003"])
        self.assertEqual(steps["left with filter"]["visible"], ["c000003"])  # 0-2 decided, nothing earlier
        self.assertEqual(steps["import"]["progress"], "4 / 6 decided")
        self.assertEqual(steps["import"]["message"],
                         "Imported 1 decision(s); skipped 1 row(s) not on this page.")
        self.assertEqual(result["download"], "decisions.csv")

        rows = list(csv.DictReader(io.StringIO(result["exported"])))
        self.assertEqual(list(rows[0].keys()), list(stitching.DECISION_FIELDS))
        self.assertEqual([(r["seam"], r["tile_a"], r["label_a"], r["tile_b"], r["label_b"], r["global_a"],
                           r["global_b"], r["decision"], r["reviewer"]) for r in rows], [
            ("0", T0, "1", T4, "1", "1", "8", "yes", "rv"),
            ("1", T0, "2", T2, "1", "2", "5", "unsure", "rv"),
            ("2", T0, "3", T1, "1", "3", "4", "yes", "rv"),
        ])
        for row in rows:
            self.assertRegex(row["decided_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")
        stored = json.loads(next(iter(result["stored"].values())))
        self.assertEqual(stored["decisions"]["5:2:2"]["decision"], "no")
        self.assertEqual(stored["reviewer"], "rv")


# --- phase 5: objects ------------------------------------------------------------

def write_decisions(path, candidates, decisions: dict[int, str], *, reviewer="r1", overrides=None):
    """decisions.csv for the candidate rows at the given indices; ``overrides``
    replaces fields per index."""
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=stitching.DECISION_FIELDS)
        writer.writeheader()
        for index, decision in decisions.items():
            row = {f: candidates[index][f] for f in stitching.DECISION_FIELDS[:7]}
            row.update(decision=decision, reviewer=reviewer, decided_at="2026-11-04T10:00:00Z")
            row.update((overrides or {}).get(index, {}))
            writer.writerow(row)
    return path


# Candidate indices: 0 A, 1 B, 2 C (x), 3 C (z), 4 G-H1, 5 G-H2.
ALL_DECIDED = {0: "yes", 1: "yes", 2: "yes", 3: "yes", 4: "yes", 5: "no"}

EXPECTED_OBJECTS = [
    # global_id, object_id
    (1, 1), (2, 2), (3, 3), (4, 3), (5, 2), (6, 6), (7, 7), (8, 1),
    (70008, 3), (70009, 6), (70010, 70010), (70011, 70011), (70012, 70012),
]

EXPECTED_SUMMARY = [
    # object_id, member_count, tiles, total_voxel_count, seam_count
    (1, 2, f"{T0};{T4}", 96, 1),            # A
    (2, 2, f"{T0};{T2}", 96, 1),            # B
    (3, 3, f"{T0};{T1};{T5}", 296, 2),      # C across three tiles
    (6, 2, f"{T2};{T6}", 336, 1),           # G + H1; H2 stays apart
    (7, 1, T3, 48, 0),                      # D
    (70010, 1, T6, 80, 0),                  # H2
    (70011, 1, T6, 72, 0),                  # E
    (70012, 1, T7, 48, 0),                  # F
]


class ObjectsTests(StitchedGridMixin, SimpleTestCase):
    def decisions(self, decisions, name="decisions.csv", **kwargs):
        return str(write_decisions(self.work / name, self.candidates, decisions, **kwargs))

    def objects(self, *paths, extra=()):
        args = ["stitch_objects", "--work", str(self.work)]
        for path in paths:
            args += ["--decisions", path]
        return self.call(*args, *extra)

    def test_expected_objects(self):
        err = self.objects(self.decisions(ALL_DECIDED))
        self.assertIn("decisions: yes 5, no 1, unsure 0, undecided 0", err)
        self.assertIn("8 object(s), 4 built from more than one label", err)
        self.assertNotIn("same tile", err)
        self.assertEqual(as_tuples(read_rows(self.work / "objects.csv"), stitching.OBJECTS_FIELDS),
                         EXPECTED_OBJECTS)
        self.assertEqual(as_tuples(read_rows(self.work / "objects_summary.csv"), stitching.OBJECTS_SUMMARY_FIELDS),
                         EXPECTED_SUMMARY)

    def test_shards_combine_and_agreeing_duplicates_are_fine(self):
        first = self.decisions({0: "yes", 1: "yes", 2: "yes"}, "a.csv")
        second = self.decisions({2: "yes", 3: "yes", 4: "yes", 5: "no"}, "b.csv", reviewer="r2")
        self.objects(first, second, extra=["--require-complete"])
        self.assertEqual(as_tuples(read_rows(self.work / "objects_summary.csv"), stitching.OBJECTS_SUMMARY_FIELDS),
                         EXPECTED_SUMMARY)

    def test_unsure_and_undecided_do_not_merge(self):
        path = self.decisions({0: "yes", 1: "unsure", 2: "no", 3: ""})
        err = self.objects(path)
        self.assertIn("decisions: yes 1, no 1, unsure 1, undecided 3", err)
        objects = dict(as_tuples(read_rows(self.work / "objects.csv"), stitching.OBJECTS_FIELDS))
        self.assertEqual((objects[1], objects[8]), (1, 1))
        self.assertEqual((objects[2], objects[5], objects[4], objects[70008]), (2, 5, 4, 70008))
        with self.assertRaisesMessage(CommandError, "3 of 6 candidate(s) are undecided"):
            self.objects(path, extra=["--require-complete"])

    def test_two_labels_from_one_tile_are_flagged(self):
        err = self.objects(self.decisions({4: "yes", 5: "yes"}))
        self.assertIn("1 object(s) hold two labels from the same tile; check them: 6", err)
        summary = as_tuples(read_rows(self.work / "objects_summary.csv"), stitching.OBJECTS_SUMMARY_FIELDS)
        self.assertIn((6, 3, f"{T2};{T6}", 416, 1), summary)

    def test_conflicts_are_listed_and_refused(self):
        first = self.decisions({0: "yes", 4: "yes"}, "a.csv")
        second = self.decisions({0: "no", 4: "yes"}, "b.csv")
        third = self.decisions({0: "unsure"}, "c.csv")
        with self.assertRaises(CommandError) as ctx:
            self.objects(first, second, third)
        message = str(ctx.exception)
        self.assertIn("conflicting decisions for the same pair (1)", message)
        self.assertIn(f"seam 0 {T0}:1 -> {T4}:1: no at {second}:2; unsure at {third}:2; yes at {first}:2", message)
        self.assertFalse((self.work / "objects.csv").exists())

    def test_rows_matching_no_candidate_are_refused(self):
        path = self.decisions({0: "yes", 1: "yes", 2: "yes"}, overrides={
            0: {"label_b": "2"},        # wrong label
            1: {"global_b": "6"},       # right pair, wrong global id
            2: {"seam": "11"},          # wrong seam
        })
        with self.assertRaises(CommandError) as ctx:
            self.objects(path)
        message = str(ctx.exception)
        self.assertIn("decisions that match no candidates.csv row (3)", message)
        for line in (2, 3, 4):
            self.assertIn(f"{path}:{line}:", message)

    def test_invalid_values_and_columns(self):
        with self.assertRaisesMessage(CommandError, "decision 'maybe' is not yes, no or unsure"):
            self.objects(self.decisions({0: "maybe"}))
        bad = self.work / "bad.csv"
        bad.write_text("seam,tile_a,label_a,decision\n0,x,1,yes\n")
        with self.assertRaisesMessage(CommandError, "missing column(s) tile_b, label_b, global_a, global_b"):
            self.objects(str(bad))

    def test_decisions_are_case_and_space_tolerant(self):
        self.objects(self.decisions({0: " Yes ", 5: "NO"}))
        objects = dict(as_tuples(read_rows(self.work / "objects.csv"), stitching.OBJECTS_FIELDS))
        self.assertEqual(objects[8], 1)


class UnionFindTests(SimpleTestCase):
    def test_root_is_the_smallest_member(self):
        uf = stitching.UnionFind([9, 5, 7, 2, 2**64 - 1])
        uf.union(9, 7)
        uf.union(2**64 - 1, 9)
        self.assertEqual(uf.find(2**64 - 1), 7)
        uf.union(7, 5)
        self.assertEqual({uf.find(x) for x in (5, 7, 9, 2**64 - 1)}, {5})
        self.assertEqual(uf.find(2), 2)


# --- phase 6: measurements, and auto decisions ----------------------------------

VOXEL = (30.0, 8.0, 8.0)

# Long objects on the same 2 x 2 x 2 layout, so skeletons are well above the dust size.
LONG_OBJECTS = (
    # L1: one tube through four tiles: X seam T0 -> T1, Y seam T1 -> T3, Z seam T3 -> T7.
    ("L1x", (3, 6, 10, 13, 4, 60), {T0: 1, T1: 1}),
    ("L1y", (3, 6, 10, 50, 57, 60), {T1: 1, T3: 1}),
    ("L1z", (3, 14, 47, 50, 57, 60), {T3: 1, T7: 1}),
    # L2: single-tile tube ending on T2's +x face; no partner in T3.
    ("L2", (2, 5, 40, 43, 2, 32), {T2: 1}),
    # L3: tube across the Z seam T0 -> T4, answered "no".
    ("L3", (1, 15, 20, 24, 20, 24), {T0: 2, T4: 1}),
    # L4: single-tile tube away from every face.
    ("L4", (9, 14, 5, 8, 40, 60), {T5: 1}),
)

# A one-layer grid with an empty grid position (y 8-16, x 8-16) and a gap (x 16-20).
E00, E01, E02 = "0-4_0-8_0-8_e", "0-4_0-8_8-16_e", "0-4_0-8_20-28_e"
E10, E12 = "0-4_8-16_0-8_e", "0-4_8-16_20-28_e"
EDGE_TILES = (E00, E01, E02, E10, E12)
EDGE_OBJECTS = (
    ("OUT", (1, 3, 0, 2, 2, 4), {E00: 1}),            # only the outer boundary (y=0)
    ("EMPTY", (1, 3, 6, 8, 10, 12), {E01: 1}),        # face next to the empty grid position
    ("GAP", (1, 3, 2, 4, 14, 16), {E01: 2}),          # face next to the gap
    ("MERGED", (1, 3, 4, 6, 6, 10), {E00: 2, E01: 3}),  # across a seam, answered yes
    ("NO", (1, 3, 6, 10, 2, 4), {E00: 3, E10: 1}),    # across a seam, answered no
    ("UNS", (1, 3, 6, 10, 22, 24), {E02: 1, E12: 1}),  # answered unsure
    ("UND", (1, 3, 6, 10, 25, 27), {E02: 2, E12: 2}),  # left undecided
)


class StitchRunMixin(TempDirMixin):
    def stitch(self, tiles_dir, decide) -> Path:
        """Phases 1-5 on ``tiles_dir``; ``decide(candidates)`` gives {index: decision}."""
        work = self.make_dir() / "work"
        self.call("stitch_inventory", "--tiles", str(tiles_dir), "--work", str(work))
        self.call("stitch_ids", "--work", str(work))
        self.call("stitch_candidates", "--work", str(work))
        candidates = read_rows(work / "candidates.csv")
        path = write_decisions(work / "decisions.csv", candidates, decide(candidates))
        self.call("stitch_objects", "--work", str(work), "--decisions", str(path))
        return work

    def measure(self, work, *extra):
        return self.call("stitch_measure", "--work", str(work), "--voxel-size-zyx", *map(str, VOXEL), *extra)

    def measurements(self, work) -> dict[int, dict]:
        return {int(r["object_id"]): r for r in read_rows(work / "measurements.csv")}

    def object_of(self, work, tile, local_id) -> int:
        (global_id,) = [int(r["global_id"]) for r in read_rows(work / "id_map.csv")
                        if r["tile"] == tile and int(r["local_id"]) == local_id]
        return {int(r["global_id"]): int(r["object_id"]) for r in read_rows(work / "objects.csv")}[global_id]

    def untiled_measurements(self, tiles_dir, work):
        """Per-volume measurements of the same objects in one untiled volume
        (each voxel labelled with its object_id)."""
        tiles = read_rows(work / "tiles.csv")
        offsets = {r["tile"]: int(r["id_offset"]) for r in read_rows(work / "tile_ids.csv")}
        object_of = {int(r["global_id"]): int(r["object_id"]) for r in read_rows(work / "objects.csv")}
        shape = tuple(max(int(r[f"{a}1"]) for r in tiles) for a in "zyx")
        volume = np.zeros(shape, np.uint32)
        for row in tiles:
            local = tifffile.imread(row["path"]).astype(np.int64)
            lookup = np.zeros(int(local.max()) + 1, np.uint32)
            for lid in np.unique(local[local > 0]).tolist():
                lookup[lid] = object_of[offsets[row["tile"]] + lid]
            box = tuple(slice(int(row[f"{a}0"]), int(row[f"{a}1"])) for a in "zyx")
            volume[box] = lookup[local]
        path = self.make_dir() / "untiled.tif"
        _write_tiff(path, volume)
        return {m.label_id: m for m in measure_label_volume(path, VOXEL)}


def decide_all(decision="yes", overrides=None):
    """Decide every candidate ``decision``, except ``overrides``:
    {(tile_a, tile_b): decision} for the candidates between those tiles."""
    return lambda candidates: {i: (overrides or {}).get((c["tile_a"], c["tile_b"]), decision)
                               for i, c in enumerate(candidates)}


class MeasureMatchesUntiledTests(StitchRunMixin, SimpleTestCase):
    """Key property: stitched measurements equal the per-volume code on the
    same objects in one untiled volume."""

    def assert_matches_untiled(self, tiles_dir, work):
        stitched = self.measurements(work)
        untiled = self.untiled_measurements(tiles_dir, work)
        self.assertEqual(set(stitched), set(untiled))
        for object_id, row in stitched.items():
            expected = untiled[object_id]
            with self.subTest(object_id=object_id):
                self.assertEqual(int(row["voxel_count"]), expected.voxel_count)
                self.assertAlmostEqual(float(row["volume_um3"]), expected.volume_um3, places=12)
                self.assertAlmostEqual(float(row["skeleton_length_um"]), expected.skeleton_length_um,
                                       delta=1e-9 * max(1.0, expected.skeleton_length_um))
        return stitched

    def test_grid_objects(self):
        tiles_dir = self.make_dir()
        build_grid(tiles_dir)
        decisions = {0: "yes", 1: "yes", 2: "yes", 3: "yes", 4: "yes", 5: "no"}
        work = self.stitch(tiles_dir, lambda candidates: decisions)
        self.measure(work)
        stitched = self.assert_matches_untiled(tiles_dir, work)
        # The three-tile object C and the G/H1 merge are above the dust size only once stitched.
        self.assertEqual((stitched[3]["member_count"], stitched[3]["tiles"]), ("3", f"{T0};{T1};{T5}"))
        self.assertEqual((stitched[6]["member_count"], stitched[6]["tiles"]), ("2", f"{T2};{T6}"))
        self.assertGreater(float(stitched[3]["skeleton_length_um"]), 0)
        self.assertGreater(float(stitched[6]["skeleton_length_um"]), 0)
        self.assertEqual(stitched[70010]["member_count"], "1")   # H2 answered no

    def test_long_objects(self):
        tiles_dir = self.make_dir()
        write_tiles(tiles_dir, GRID_TILES, LONG_OBJECTS)
        work = self.stitch(tiles_dir, decide_all(overrides={(T0, T4): "no"}))
        self.measure(work)
        stitched = self.assert_matches_untiled(tiles_dir, work)
        l1 = stitched[self.object_of(work, T0, 1)]
        self.assertEqual((l1["member_count"], l1["tiles"]), ("4", f"{T0};{T1};{T3};{T7}"))
        self.assertGreater(float(l1["skeleton_length_um"]), 0.5)
        l3 = {self.object_of(work, T0, 2), self.object_of(work, T4, 1)}
        self.assertEqual(len(l3), 2)
        for object_id in (*l3, self.object_of(work, T2, 1), self.object_of(work, T5, 1)):
            self.assertGreater(float(stitched[object_id]["skeleton_length_um"]), 0)
        self.assertEqual({r["touches_edge"] for r in stitched.values()}, {"false"})


class MeasureOutputTests(StitchRunMixin, SimpleTestCase):
    def setUp(self):
        self.tiles_dir = self.make_dir()
        build_grid(self.tiles_dir)

    def test_rows_and_progress_log(self):
        work = self.stitch(self.tiles_dir, lambda c: ALL_DECIDED)
        err = self.measure(work)
        self.assertIn(f"tile 1/8 {T0}: 3 label(s), 0 single-tile object(s) measured", err)
        self.assertIn(f"tile 4/8 {T3}: 1 label(s), 1 single-tile object(s) measured", err)
        self.assertRegex(err, r"object 2/4 2: 2 label\(s\) in 2 tiles, crop 7x12x10, [\d.e+-]+ um")
        self.assertIn("8 object(s) (4 multi-tile)", err)
        rows = read_rows(work / "measurements.csv")
        self.assertEqual(list(rows[0]), list(stitching_measure.MEASUREMENTS_FIELDS))
        self.assertEqual([int(r["object_id"]) for r in rows], [1, 2, 3, 6, 7, 70010, 70011, 70012])
        c = self.measurements(work)[3]
        self.assertEqual({k: c[k] for k in ("member_count", "tiles", "voxel_count", "touches_edge", "edge_faces",
                                            "skipped_reason", "unresolved_contacts", "unproofread_tiles")},
                         {"member_count": "3", "tiles": f"{T0};{T1};{T5}", "voxel_count": "296",
                          "touches_edge": "false", "edge_faces": "", "skipped_reason": "",
                          "unresolved_contacts": "0", "unproofread_tiles": "0"})
        self.assertEqual(float(c["volume_um3"]), 296 * 30 * 8 * 8 / 1e9)
        # In the full grid only D reaches the outer boundary.
        edges = {o: r["edge_faces"] for o, r in self.measurements(work).items() if r["touches_edge"] == "true"}
        self.assertEqual(edges, {7: f"{T3}:-z;{T3}:+y;{T3}:+x"})

    def test_crop_limit_skips_length_only(self):
        work = self.stitch(self.tiles_dir, lambda c: ALL_DECIDED)
        err = self.measure(work, "--max-crop-voxels", "1,000")
        self.assertIn("crop 14x8x18 = 2016 voxels > 1000; skipped (crop_limit)", err)
        rows = self.measurements(work)
        skipped = {o for o, r in rows.items() if r["skipped_reason"]}
        self.assertEqual(skipped, {3, 6})   # C: 14x8x18 = 2016, G+H1: 10x12x12 = 1440 padded voxels
        for o in skipped:
            self.assertEqual((rows[o]["skipped_reason"], rows[o]["skeleton_length_um"]), ("crop_limit", ""))
            self.assertEqual(float(rows[o]["volume_um3"]), int(rows[o]["voxel_count"]) * 30 * 8 * 8 / 1e9)
        self.assertEqual(rows[1]["skeleton_length_um"], "0.0")   # A: 640 padded voxels, under the cap
        with self.assertRaisesMessage(CommandError, "--max-crop-voxels must be positive"):
            self.measure(work, "--max-crop-voxels", "0")

    def test_unresolved_contacts(self):
        work = self.stitch(self.tiles_dir, lambda c: {0: "yes", 1: "unsure", 2: "yes", 4: "yes", 5: "no"})
        self.measure(work)
        unresolved = {o: int(r["unresolved_contacts"]) for o, r in self.measurements(work).items()}
        self.assertEqual(unresolved, {1: 0, 2: 1, 5: 1, 3: 1, 70008: 1, 6: 0, 7: 0,
                                      70010: 0, 70011: 0, 70012: 0})
        listed = read_rows(work / "unresolved_candidates.csv")
        self.assertEqual([(r["seam"], r["current_decision"], r["object_a"], r["object_b"]) for r in listed],
                         [("1", "unsure", "2", "5"), ("3", "undecided", "3", "70008")])

    def test_unproofread_tiles(self):
        work = self.stitch(self.tiles_dir, lambda c: ALL_DECIDED)
        listing = work / "unproofread.txt"
        listing.write_text(f"# not proofread yet\n\n/links/8-16_0-32_32-64_prediction.h5\n{T0}\n")
        self.measure(work, "--unproofread-tiles", str(listing))
        counts = {o: int(r["unproofread_tiles"]) for o, r in self.measurements(work).items()}
        self.assertEqual(counts, {1: 1, 2: 1, 3: 2, 6: 0, 7: 0, 70010: 0, 70011: 0, 70012: 0})
        listing.write_text("99-100_0-32_0-32_proofread.tif\n")
        with self.assertRaisesMessage(CommandError, "unproofread tiles not in tiles.csv"):
            self.measure(work, "--unproofread-tiles", str(listing))

    def test_refuses_bad_inputs(self):
        work = self.stitch(self.tiles_dir, lambda c: ALL_DECIDED)
        with self.assertRaisesMessage(CommandError, "voxel size must be positive"):
            self.call("stitch_measure", "--work", str(work), "--voxel-size-zyx", "0", "8", "8")
        objects = work / "objects.csv"
        objects.write_text(objects.read_text().replace("\n8,1\n", "\n8,8\n"))
        with self.assertRaisesMessage(CommandError, "rerun stitch_objects"):
            self.measure(work)
        (work / "decisions_resolved.csv").unlink()
        with self.assertRaisesMessage(CommandError, "decisions_resolved.csv not found"):
            self.measure(work)

    def test_decisions_resolved_records_every_candidate(self):
        work = self.stitch(self.tiles_dir, lambda c: {0: "yes", 1: "unsure", 5: "no"})
        rows = read_rows(work / "decisions_resolved.csv")
        self.assertEqual(list(rows[0]), list(stitching.RESOLVED_FIELDS))
        self.assertEqual([r["decision"] for r in rows], ["yes", "unsure", "undecided", "undecided", "undecided", "no"])


class EdgeRuleTests(StitchRunMixin, SimpleTestCase):
    def test_faces_without_a_neighbouring_tile_are_edges(self):
        tiles_dir = self.make_dir()
        write_tiles(tiles_dir, EDGE_TILES, EDGE_OBJECTS)
        decisions = {(E00, E01): "yes", (E00, E10): "no"}
        work = self.stitch(tiles_dir, lambda candidates: {
            i: decisions.get((c["tile_a"], c["tile_b"]), "unsure" if c["label_a"] == "1" else "")
            for i, c in enumerate(candidates)})
        issues = {r["kind"] for r in read_rows(work / "issues.csv")}
        self.assertTrue({"gap", "missing_position"} <= issues)
        self.measure(work)
        rows = self.measurements(work)

        def row(tile, local_id):
            return rows[self.object_of(work, tile, local_id)]

        expected = {
            (E00, 1): ("true", f"{E00}:-y"),     # OUT: outer boundary
            (E01, 1): ("true", f"{E01}:+y"),     # EMPTY: next to the empty grid position
            (E01, 2): ("true", f"{E01}:+x"),     # GAP: next to the gap
            (E00, 2): ("false", ""),             # MERGED across a seam
            (E00, 3): ("false", ""),             # NO: seam face, pair answered no
            (E10, 1): ("false", ""),
            (E02, 1): ("false", ""), (E12, 1): ("false", ""),   # UNS
            (E02, 2): ("false", ""), (E12, 2): ("false", ""),   # UND
        }
        for (tile, local_id), (touches, faces) in expected.items():
            with self.subTest(tile=tile, local_id=local_id):
                self.assertEqual((row(tile, local_id)["touches_edge"], row(tile, local_id)["edge_faces"]),
                                 (touches, faces))
        self.assertEqual(row(E00, 2)["member_count"], "2")
        self.assertEqual({(t, l): row(t, l)["unresolved_contacts"] for t, l in
                          ((E02, 1), (E12, 1), (E02, 2), (E12, 2), (E00, 3))},
                         {(E02, 1): "1", (E12, 1): "1", (E02, 2): "1", (E12, 2): "1", (E00, 3): "0"})

    def test_partly_covered_face(self):
        tiles_dir = self.make_dir()
        tall, short = "0-4_0-16_0-8_p", "0-4_0-8_8-16_p"
        write_tiles(tiles_dir, (tall, short), (
            ("COVERED", (1, 3, 2, 4, 6, 10), {tall: 1, short: 1}),   # +x face where the seam is
            ("UNCOVERED", (1, 3, 10, 12, 6, 8), {tall: 2}),           # +x face beyond the short tile
        ))
        work = self.stitch(tiles_dir, lambda candidates: {0: "no"})
        self.measure(work)
        self.assertEqual(self.measurements(work)[self.object_of(work, tall, 1)]["edge_faces"], "")
        self.assertEqual(self.measurements(work)[self.object_of(work, tall, 2)]["edge_faces"], f"{tall}:+x")


class AutoDecisionTests(StitchedGridMixin, SimpleTestCase):
    # Grid candidates: 0 A (16 voxels, 1/1), 1 B (6, .5/.5), 2 C (12, 1/1), 3 C (16, 1/1),
    # 4 G-H1 (40, .625/1), 5 G-H2 (24, .375/.6); G has two partners on seam 5.

    def accepted(self, **kwargs):
        return stitching.auto_accept(self.candidates, **kwargs)

    def test_thresholds_and_one_to_one(self):
        self.assertEqual(self.accepted(min_frac=0.5, min_contact=50), [])
        self.assertEqual(self.accepted(min_frac=0.5, min_contact=10), [0, 2, 3])
        self.assertEqual(self.accepted(min_frac=0.5, min_contact=6), [0, 1, 2, 3])   # both bounds inclusive
        self.assertEqual(self.accepted(min_frac=0.6, min_contact=1), [0, 2, 3])
        self.assertEqual(self.accepted(min_frac=0.0, min_contact=0), [0, 1, 2, 3])   # G-H1/G-H2 never

    def test_one_to_one_counts_both_sides(self):
        rows = [{"seam": "0", "label_a": "1", "label_b": "1"}, {"seam": "0", "label_a": "2", "label_b": "1"},
                {"seam": "0", "label_a": "3", "label_b": "3"}, {"seam": "1", "label_a": "3", "label_b": "3"}]
        self.assertEqual(stitching.one_to_one(rows), [False, False, True, True])

    def test_command_output_is_accepted_by_stitch_objects(self):
        err = self.call("stitch_auto_decisions", "--work", str(self.work), "--min-contact", "10")
        self.assertIn("3 of 6 candidate(s) accepted (auto:one-to-one,min_frac=0.5,min_contact=10)", err)
        rows = read_rows(self.work / "decisions_auto.csv")
        self.assertEqual(list(rows[0]), list(stitching.DECISION_FIELDS))
        self.assertEqual([(r["seam"], r["label_a"], r["label_b"], r["decision"], r["reviewer"]) for r in rows], [
            ("0", "1", "1", "yes", "auto:one-to-one,min_frac=0.5,min_contact=10"),
            ("2", "3", "1", "yes", "auto:one-to-one,min_frac=0.5,min_contact=10"),
            ("3", "1", "70000", "yes", "auto:one-to-one,min_frac=0.5,min_contact=10")])
        self.assertRegex(rows[0]["decided_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        human = write_decisions(self.work / "human.csv", self.candidates, {1: "yes", 4: "yes", 5: "no"})
        self.call("stitch_objects", "--work", str(self.work), "--decisions", str(self.work / "decisions_auto.csv"),
                  "--decisions", str(human), "--require-complete")
        self.assertEqual(as_tuples(read_rows(self.work / "objects_summary.csv"), stitching.OBJECTS_SUMMARY_FIELDS),
                         EXPECTED_SUMMARY)
        # A spot check that disagrees with the rule is a conflict, not a silent override.
        check = write_decisions(self.work / "check.csv", self.candidates, {2: "no"})
        with self.assertRaisesMessage(CommandError, "conflicting decisions for the same pair (1)"):
            self.call("stitch_objects", "--work", str(self.work), "--decisions",
                      str(self.work / "decisions_auto.csv"), "--decisions", str(check))

    def test_sample_is_reproducible_and_reviewable(self):
        args = ("stitch_auto_decisions", "--work", str(self.work), "--min-contact", "6", "--sample", "2", "--seed", "7")
        err = self.call(*args)
        sample = self.work / "auto_sample_2_seed7.csv"
        self.assertIn(f"sample of 2 (seed 7) -> {sample}", err)
        first = sample.read_text()
        self.call(*args)
        self.assertEqual(sample.read_text(), first)
        rows = read_rows(sample)
        self.assertEqual(list(rows[0]), list(stitching.AUTO_SAMPLE_FIELDS))
        indices = [next(i for i, c in enumerate(self.candidates) if (c["seam"], c["label_a"], c["label_b"]) ==
                        (r["seam"], r["label_a"], r["label_b"])) for r in rows]
        self.assertEqual(indices, sorted(indices))
        self.assertTrue(set(indices) <= {0, 1, 2, 3})
        everything = stitching.run_auto_decisions(self.work, min_contact=6, sample=10, seed=1)
        self.assertEqual(everything["sample"], 4)

        images = self.images()
        self.call("stitch_review", "--work", str(self.work), "--images", str(images), "--crop", "8",
                  "--only", str(sample))
        page = (self.work / "review_auto_sample_2_seed7" / "index.html").read_text()
        data = json.loads(re.search(r'<script type="application/json" id="review-data">(.*?)</script>',
                                    page, re.S).group(1))
        self.assertEqual([c["id"] for c in data["cards"]], [f"c{i:06d}" for i in indices])
        self.assertEqual(data["export_name"], "decisions_auto_sample_2_seed7.csv")
        bad = self.work / "bad_sample.csv"
        bad.write_text(sample.read_text().replace(f",{T0},1,{T4},1,", f",{T0},1,{T4},9,"))
        with self.assertRaisesMessage(CommandError, "--only rows that match no candidates.csv row"):
            self.call("stitch_review", "--work", str(self.work), "--images", str(images), "--only", str(bad))


# --- region masks (ROI) -----------------------------------------------------------

# Region boxes painted 255 (nonzero = inside), in region coordinates. T4 has no
# region file at all; T3 and T5-T7 have files that are entirely outside.
GRID_REGIONS = {
    T0: [(0, 8, 0, 6, 0, 32)],      # y < 6: cuts A (24 of its 48 voxels in T0)
    T1: [(0, 8, 0, 32, 32, 64)],    # the whole tile: C's 160 voxels there
    T2: [(0, 8, 40, 64, 0, 32)],    # y >= 40: all of G, none of B
    T3: [], T5: [], T6: [], T7: [],
}
EXTRA_REGION = "16-24_0-32_0-32"    # a region file with no tile

EXPECTED_REGION_ID_MAP = [
    # tile, local_id, global_id, voxel_count, region_voxels
    (T0, 1, 1, 48, 24), (T0, 2, 2, 48, 0), (T0, 3, 3, 72, 0), (T1, 1, 4, 160, 160),
    (T2, 1, 5, 48, 0), (T2, 2, 6, 256, 256), (T3, 1, 7, 48, 0), (T4, 1, 8, 48, 0),
    (T5, 70000, 70008, 64, 0), (T6, 1, 70009, 80, 0), (T6, 2, 70010, 80, 0), (T6, 3, 70011, 72, 0),
    (T7, 1, 70012, 48, 0),
]
# Region size per tile: T0 8x6x32, T1 8x32x32, T2 8x24x32; others none.
EXPECTED_TILE_REGION_VOXELS = [(T0, 1536), (T1, 8192), (T2, 6144), (T3, 0), (T4, 0), (T5, 0), (T6, 0), (T7, 0)]
# Candidates 0 A, 1 B, 2 C, 3 C, 4 G-H1, 5 G-H2: only B lies wholly outside.
EXPECTED_IN_REGION = ["true", "false", "true", "true", "true", "true"]


def build_regions(directory: Path, regions=GRID_REGIONS, *, suffix="_mask_pc2.tif", hdf5=False, shapes=None):
    for name, boxes in regions.items():
        _, box = parse_tile_name(name)
        shape = (shapes or {}).get(name) or tuple(box[2 * a + 1] - box[2 * a] for a in range(3))
        mask = np.zeros(shape, np.uint8)
        for r in boxes:
            mask[tuple(slice(r[2 * a] - box[2 * a], r[2 * a + 1] - box[2 * a]) for a in range(3))] = 255
        stem = name[:-len("_im")]
        if hdf5:
            with h5py.File(directory / f"{stem}{suffix}", "w") as fh:
                fh.create_dataset("mask", data=mask, chunks=(1,) + shape[1:])
        else:
            _write_tiff(directory / f"{stem}{suffix}", mask)


def expected_csv(fields, rows) -> str:
    return "".join(",".join(map(str, row)) + "\r\n" for row in [fields, *rows])


class RegionTests(StitchedGridMixin, SimpleTestCase):
    def regions(self, **kwargs) -> Path:
        directory = self.make_dir()
        build_regions(directory, **kwargs)
        _write_tiff(directory / f"{EXTRA_REGION}_mask_pc2.tif", np.zeros((8, 32, 32), np.uint8))
        return directory

    def with_regions(self, regions=None) -> str:
        err = self.call("stitch_ids", "--work", str(self.work), "--regions", str(regions or self.regions()))
        self.call("stitch_candidates", "--work", str(self.work))
        self.candidates = read_rows(self.work / "candidates.csv")
        return err

    def test_region_voxels_per_label_and_tile(self):
        err = self.with_regions()
        self.assertIn(f"no region file for tile {T4}; counted as outside", err)
        self.assertIn("matches no tile", err)
        self.assertIn("7 tile(s) with a region, 1 without, 1 region file(s) without a tile; "
                      "3 label(s) reach into the region", err)
        id_map = read_rows(self.work / "id_map.csv")
        self.assertEqual(list(id_map[0]), [*stitching.ID_MAP_FIELDS, "region_voxels"])
        self.assertEqual(as_tuples(id_map, (*stitching.ID_MAP_FIELDS, "region_voxels")), EXPECTED_REGION_ID_MAP)
        tile_ids = read_rows(self.work / "tile_ids.csv")
        self.assertEqual(list(tile_ids[0]), [*stitching.TILE_IDS_FIELDS, "region_voxels"])
        self.assertEqual(as_tuples(tile_ids, ("tile", "region_voxels")), EXPECTED_TILE_REGION_VOXELS)
        # Everything phase 2 wrote before is unchanged.
        self.assertEqual(as_tuples(id_map, ("tile", "local_id", "global_id", "voxel_count")), EXPECTED_ID_MAP)
        pairing = read_rows(self.work / "region_pairing.csv")
        self.assertEqual([(r["tile"], r["region_tile"], r["status"]) for r in pairing], [
            *[(t, t[:-len("_im")] + "_mask_pc2", "matched") for t in (T0, T1, T2, T3)],
            (T4, "", "no_region"),
            *[(t, t[:-len("_im")] + "_mask_pc2", "matched") for t in (T5, T6, T7)],
            ("", f"{EXTRA_REGION}_mask_pc2", "no_tile"),
        ])

    def test_region_is_read_in_the_label_blocks(self):
        reads = []
        real = stitching._count_blocks

        class Watch:
            def __init__(self, region):
                self.region, self.shape, self.dtype = region, region.shape, region.dtype

            def __getitem__(self, key):
                reads.append((key.start, key.stop))
                return self.region[key]

        def spy(volume, region, where):
            return real(volume, None if region is None else Watch(region), where)

        # Blocks of 3 planes for the uint16 tiles, 1 plane for the uint32 tile T5.
        with mock.patch.object(stitching, "Z_BLOCK_BYTES", 3 * 32 * 32 * 2), \
                mock.patch.object(stitching, "_count_blocks", spy):
            self.with_regions()
        self.assertEqual(set(reads), {(0, 3), (3, 6), (6, 9)} | {(z, z + 1) for z in range(8)})
        self.assertEqual(as_tuples(read_rows(self.work / "id_map.csv"), (*stitching.ID_MAP_FIELDS, "region_voxels")),
                         EXPECTED_REGION_ID_MAP)

    @skipUnless(h5py is not None, "h5py is not installed")
    def test_hdf5_region_masks(self):
        directory = self.make_dir()
        build_regions(directory, suffix="_mask_pc2.h5", hdf5=True)
        self.with_regions(directory)
        self.assertEqual(as_tuples(read_rows(self.work / "id_map.csv"), (*stitching.ID_MAP_FIELDS, "region_voxels")),
                         EXPECTED_REGION_ID_MAP)

    def test_region_shape_mismatch_is_refused(self):
        directory = self.make_dir()
        build_regions(directory, shapes={T2: (8, 32, 31)})
        with self.assertRaisesMessage(CommandError, "has shape 8x32x31 but tile 0-8_32-64_0-32_im is 8x32x32"):
            self.call("stitch_ids", "--work", str(self.work), "--regions", str(directory))

    def test_in_region(self):
        self.with_regions()
        self.assertEqual(list(self.candidates[0]), [*stitching.CANDIDATES_FIELDS, "in_region"])
        self.assertEqual([c["in_region"] for c in self.candidates], EXPECTED_IN_REGION)
        self.assertEqual(as_tuples(self.candidates, stitching.CANDIDATES_FIELDS), EXPECTED_CANDIDATES)

    def test_without_regions_outputs_are_unchanged(self):
        def raw(name):
            with open(self.work / name, newline="") as fh:
                return fh.read()

        def check_plain():
            self.assertEqual(raw("candidates.csv"), expected_csv(stitching.CANDIDATES_FIELDS, EXPECTED_CANDIDATES))
            self.assertEqual(raw("id_map.csv"), expected_csv(stitching.ID_MAP_FIELDS, EXPECTED_ID_MAP))
            self.assertEqual(stitching.csv_fields(self.work / "tile_ids.csv"), list(stitching.TILE_IDS_FIELDS))
            self.assertFalse((self.work / "region_pairing.csv").exists())

        check_plain()
        self.with_regions()
        self.call("stitch_ids", "--work", str(self.work))          # rerun without --regions
        self.call("stitch_candidates", "--work", str(self.work))
        check_plain()

    def test_region_only_review_cards(self):
        self.with_regions()
        images = self.images()
        self.call("stitch_review", "--work", str(self.work), "--images", str(images), "--crop", "8", "--region-only")
        page = (self.work / "review_region" / "index.html").read_text()
        data = json.loads(re.search(r'<script type="application/json" id="review-data">(.*?)</script>',
                                    page, re.S).group(1))
        self.assertEqual([c["id"] for c in data["cards"]], ["c000000", "c000002", "c000003", "c000004", "c000005"])
        self.assertEqual(data["export_name"], "decisions_region.csv")
        self.assertIn("<title>Seam review (region only)</title>", page)
        everything = stitching_review.build_review(self.work, [images], self.make_dir() / "r", crop=8)
        self.assertEqual(len(everything.cards), 6)

    def test_region_only_needs_region_data(self):
        images = self.images()
        message = "candidates.csv has no in_region column; run stitch_ids --regions"
        with self.assertRaisesMessage(CommandError, message):
            self.call("stitch_review", "--work", str(self.work), "--images", str(images), "--region-only")
        with self.assertRaisesMessage(CommandError, message):
            self.call("stitch_auto_decisions", "--work", str(self.work), "--region-only")
        write_decisions(self.work / "d.csv", self.candidates, ALL_DECIDED)
        self.call("stitch_objects", "--work", str(self.work), "--decisions", str(self.work / "d.csv"))
        with self.assertRaisesMessage(CommandError, "--region-only needs region data"):
            self.call("stitch_measure", "--work", str(self.work), "--voxel-size-zyx", "30", "8", "8", "--region-only")

    def test_region_only_auto_decisions(self):
        self.with_regions()
        self.assertEqual(stitching.auto_accept(self.candidates, min_frac=0.5, min_contact=6), [0, 1, 2, 3])
        self.assertEqual(stitching.auto_accept(self.candidates, min_frac=0.5, min_contact=6, region_only=True),
                         [0, 2, 3])
        err = self.call("stitch_auto_decisions", "--work", str(self.work), "--min-contact", "6", "--region-only")
        self.assertIn("3 of 6 candidate(s) accepted (auto:one-to-one,min_frac=0.5,min_contact=6,region_only)", err)
        rows = read_rows(self.work / "decisions_auto.csv")
        self.assertEqual([r["seam"] for r in rows], ["0", "2", "3"])
        self.assertEqual({r["reviewer"] for r in rows}, {"auto:one-to-one,min_frac=0.5,min_contact=6,region_only"})

    def test_partner_outside_the_region_still_breaks_one_to_one(self):
        rows = [
            {"seam": "0", "label_a": "1", "label_b": "5", "frac_a": "1.0", "frac_b": "0.9", "contact_voxels": "80",
             "in_region": "true"},
            {"seam": "0", "label_a": "2", "label_b": "5", "frac_a": "1.0", "frac_b": "0.1", "contact_voxels": "9",
             "in_region": "false"},
        ]
        self.assertEqual(stitching.auto_accept(rows, min_frac=0.5, min_contact=50, region_only=True), [])

    # Decisions: A, C (both seams) yes; G-H1 no; B and G-H2 left undecided.
    DECISIONS = {0: "yes", 2: "yes", 3: "yes", 4: "no"}
    REGION_COLUMNS = {
        # object: (region_voxels, frac_in_region, crosses_region_boundary)
        1: ("24", "0.25", "true"),                       # A: 24 of 96 inside
        3: ("160", repr(160 / 296), "true"),             # C: its T1 piece inside
        6: ("256", "1.0", "false"),                      # G alone, entirely inside
    }

    def test_region_columns_on_all_objects(self):
        self.with_regions()
        path = write_decisions(self.work / "d.csv", self.candidates, self.DECISIONS)
        self.call("stitch_objects", "--work", str(self.work), "--decisions", str(path))
        self.call("stitch_measure", "--work", str(self.work), "--voxel-size-zyx", "30", "8", "8")
        rows = {int(r["object_id"]): r for r in read_rows(self.work / "measurements.csv")}
        self.assertEqual(stitching.csv_fields(self.work / "measurements.csv"),
                         [*stitching_measure.MEASUREMENTS_FIELDS, "region_voxels", "frac_in_region",
                          "crosses_region_boundary"])
        self.assertEqual(sorted(rows), [1, 2, 3, 5, 6, 7, 70009, 70010, 70011, 70012])
        for object_id, row in rows.items():
            expected = self.REGION_COLUMNS.get(object_id, ("0", "0.0", "false"))
            with self.subTest(object_id=object_id):
                self.assertEqual((row["region_voxels"], row["frac_in_region"], row["crosses_region_boundary"]),
                                 expected)
        unresolved = {o: r["unresolved_contacts"] for o, r in rows.items() if r["unresolved_contacts"] != "0"}
        self.assertEqual(unresolved, {2: "1", 5: "1", 6: "1", 70010: "1"})   # B and G-H2 undecided

    def test_region_only_measurements(self):
        self.with_regions()
        path = write_decisions(self.work / "d.csv", self.candidates, self.DECISIONS)
        self.call("stitch_objects", "--work", str(self.work), "--decisions", str(path))
        measure = ("stitch_measure", "--work", str(self.work), "--voxel-size-zyx", "30", "8", "8")
        self.call(*measure)
        everything = {int(r["object_id"]): r for r in read_rows(self.work / "measurements.csv")}
        err = self.call(*measure, "--region-only")
        self.assertIn(f"tile 4/8 {T3}: 1 label(s), 0 single-tile object(s) measured", err)   # D is outside
        self.assertIn("3 object(s) (2 multi-tile)", err)
        self.assertIn("region only, 2 cross the region boundary", err)
        rows = {int(r["object_id"]): r for r in read_rows(self.work / "measurements.csv")}
        self.assertEqual(sorted(rows), [1, 3, 6])
        # B is outside and undecided: not listed, and not counted as unresolved anywhere.
        # G-H2 is undecided but in the region (G is inside): it still counts for object 6.
        self.assertEqual({o: r["unresolved_contacts"] for o, r in rows.items()}, {1: "0", 3: "0", 6: "1"})
        self.assertEqual([(r["seam"], r["object_a"], r["object_b"])
                          for r in read_rows(self.work / "unresolved_candidates.csv")], [("5", "6", "70010")])
        for object_id, row in rows.items():
            with self.subTest(object_id=object_id):
                self.assertEqual({k: v for k, v in row.items() if k != "unresolved_contacts"},
                                 {k: v for k, v in everything[object_id].items() if k != "unresolved_contacts"})

    def test_truncated_object_in_the_region_counts_its_undecided_contact(self):
        # A/B/C: only T5 has a region (all of it). A = C's T5 piece (inside), B = its T1 piece
        # (no region voxels), merged with A at seam 3; C = its T0 piece, with B-C (seam 2)
        # left undecided. Neither B nor C reaches into the region, but A+B does, so the
        # undecided contact means A+B may be truncated: it counts and is listed.
        directory = self.make_dir()
        build_regions(directory, {T5: [(8, 16, 0, 32, 32, 64)]})
        self.with_regions(directory)
        self.assertEqual([c["in_region"] for c in self.candidates], ["false", "false", "false", "true", "false", "false"])
        path = write_decisions(self.work / "d.csv", self.candidates, {3: "yes"})
        self.call("stitch_objects", "--work", str(self.work), "--decisions", str(path))
        measure = ("stitch_measure", "--work", str(self.work), "--voxel-size-zyx", "30", "8", "8")
        self.call(*measure)
        self.assertEqual(self.measurements_row(4)["unresolved_contacts"], "1")
        self.assertEqual([r["seam"] for r in read_rows(self.work / "unresolved_candidates.csv")],
                         ["0", "1", "2", "5", "5"])                    # every unsure/undecided candidate

        err = self.call(*measure, "--region-only")
        self.assertIn("1 unresolved candidate(s) -> unresolved_candidates.csv", err)
        rows = {int(r["object_id"]): r for r in read_rows(self.work / "measurements.csv")}
        self.assertEqual(sorted(rows), [4])                      # A+B: 64 of 224 voxels inside
        self.assertEqual((rows[4]["region_voxels"], rows[4]["crosses_region_boundary"]), ("64", "true"))
        self.assertEqual(rows[4]["unresolved_contacts"], "1")    # B-C is undecided
        unresolved = read_rows(self.work / "unresolved_candidates.csv")
        self.assertEqual(list(unresolved[0]), list(stitching_measure.UNRESOLVED_FIELDS))
        self.assertEqual([tuple(r.values()) for r in unresolved], [
            ("2", T0, "3", T1, "1", "3", "4", "3", "4", "undecided", "12", "1.000000", "1.000000")])

        # A second review round covers exactly that candidate.
        self.call("stitch_review", "--work", str(self.work), "--images", str(self.images()), "--crop", "8",
                  "--only", str(self.work / "unresolved_candidates.csv"))
        page = (self.work / "review_unresolved_candidates" / "index.html").read_text()
        data = json.loads(re.search(r'<script type="application/json" id="review-data">(.*?)</script>',
                                    page, re.S).group(1))
        self.assertEqual([c["id"] for c in data["cards"]], ["c000002"])
        self.assertEqual(data["export_name"], "decisions_unresolved_candidates.csv")

    def measurements_row(self, object_id):
        return {int(r["object_id"]): r for r in read_rows(self.work / "measurements.csv")}[object_id]
