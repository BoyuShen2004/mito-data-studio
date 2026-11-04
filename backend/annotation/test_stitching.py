"""Stitching phases 1-3 on synthetic tiles built in temporary directories.

The main fixture is a 2 x 2 x 2 grid of 8 x 32 x 32 tiles (region 16 x 64 x 64)
with hand-placed objects; every expected row below was worked out by hand from
the boxes in ``GRID_OBJECTS``.
"""

import csv
import io
import os
import tempfile
from pathlib import Path
from unittest import mock, skipUnless

import numpy as np
import tifffile
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from annotation import stitching
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
