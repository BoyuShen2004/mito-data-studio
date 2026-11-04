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

from annotation import stitching, stitching_review
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
    arrays = {}
    for name in GRID_TILES:
        _, box = parse_tile_name(name)
        arrays[name] = (box, np.zeros((8, 32, 32), dtype=GRID_DTYPES.get(name, np.uint16)))
    for obj, obox, local_ids in GRID_OBJECTS:
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
const buttons = ["yes", "no", "unsure"].map(d => { const b = el("button-" + d); b.attrs["data-decision"] = d; return b; });
JSON.parse(DATA).cards.forEach(c => { el(c.id).hidden = true; });
const docListeners = {};
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
__SCRIPT__
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

    def run_page(self):
        out = self.make_dir() / "review"
        stitching_review.build_review(self.work, [self.images()], out, crop=8)
        page = (out / "index.html").read_text()
        data = re.search(r'<script type="application/json" id="review-data">(.*?)</script>', page, re.S).group(1)
        script = re.search(r"<script>\n(.*?)</script>", page, re.S).group(1)
        harness = (self.HARNESS.replace("__DATA__", json.dumps(data)).replace("__SCRIPT__", script)
                   .replace("__T2__", T2).replace("__T6__", T6))
        path = out.parent / "harness.js"
        path.write_text(harness)
        check = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        run = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

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
