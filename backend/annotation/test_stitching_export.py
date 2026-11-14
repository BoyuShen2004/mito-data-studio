"""stitch_export: round trips of small synthetic stitched sets.

Tiles are written to a temporary directory at a nonzero origin, stitched with
the real commands, exported, and read back with tensorstore. Every voxel must
hold its object's ID at its dataset coordinate. No data is committed.
"""

import json
from pathlib import Path
from unittest import skipUnless

import numpy as np
import tifffile
from django.core.management import CommandError
from django.test import SimpleTestCase

from annotation import stitching_export, volume_measure
from annotation.stitching import Tile, parse_tile_name
from annotation.test_stitching import StitchRunMixin, decide_all, read_rows, write_tiles

try:
    import tensorstore as ts
except ImportError:  # pragma: no cover - environment problem
    ts = None

# A 2 x 2 x 2 layout at origin (96, 64, 32) with one position missing (P011)
# and tiles of two sizes: the upper Z layer is 12 deep, the lower 8.
P000, P001 = "96-104_64-96_32-64_p", "96-104_64-96_64-96_p"
P010 = "96-104_96-128_32-64_p"
P100, P101 = "104-116_64-96_32-64_p", "104-116_64-96_64-96_p"
P110, P111 = "104-116_96-128_32-64_p", "104-116_96-128_64-96_p"
TILES = (P000, P001, P010, P100, P101, P110, P111)
ORIGIN = (96, 64, 32)
SHAPE = (20, 64, 64)
OBJECTS = (
    # A: across the X seam P000 -> P001 and on through the Z seam P001 -> P101 (three tiles).
    ("A1", (98, 101, 70, 74, 56, 72), {P000: 1, P001: 1}),
    ("A2", (98, 110, 70, 74, 68, 72), {P001: 1, P101: 7}),
    # B: across the Y seam P000 -> P010; answered "no" in one test.
    ("B", (100, 103, 90, 102, 40, 44), {P000: 2, P010: 1}),
    # C: single tile, away from every face.
    ("C", (108, 112, 104, 110, 70, 80), {P111: 1}),
    # D: on P010's +x face, next to the missing position: no partner.
    ("D", (97, 100, 110, 114, 58, 64), {P010: 2}),
    # E: across the Z seam P000 -> P100 between tiles of different depth.
    ("E", (102, 108, 80, 84, 36, 40), {P000: 3, P100: 1}),
)
VOXEL_ARGS = ("--voxel-size-zyx", "30", "8", "8")


def read_layer(path: Path) -> tuple[np.ndarray, dict]:
    """The whole layer as a (Z, Y, X) array, with its ``info``."""
    info = json.loads((path / "info").read_text())
    store = ts.open({"driver": "neuroglancer_precomputed", "kvstore": {"driver": "file", "path": str(path) + "/"}},
                    read=True).result()
    return np.asarray(store[..., 0].read().result()).transpose(2, 1, 0), info


@skipUnless(ts is not None, "tensorstore is not installed")
class ExportRoundTripTests(StitchRunMixin, SimpleTestCase):
    def setUp(self):
        self.tiles_dir = self.make_dir()
        write_tiles(self.tiles_dir, TILES, OBJECTS)
        self.out = self.make_dir() / "layer"

    def export(self, work, *extra):
        return self.call("stitch_export", "--work", str(work), "--out", str(self.out), *VOXEL_ARGS,
                         "--chunk-size-xyz", "16", "16", "8", *extra)

    def expected(self, work, ids="object") -> np.ndarray:
        """Every tile's labels mapped through id_map.csv / objects.csv, placed at its box."""
        offsets = {r["tile"]: int(r["id_offset"]) for r in read_rows(work / "tile_ids.csv")}
        object_of = {int(r["global_id"]): int(r["object_id"]) for r in read_rows(work / "objects.csv")}
        volume = np.zeros(SHAPE, np.uint64)
        for row in read_rows(work / "tiles.csv"):
            local = tifffile.imread(row["path"])
            _, box = parse_tile_name(row["tile"])
            target = volume[tuple(slice(box[2 * a] - ORIGIN[a], box[2 * a + 1] - ORIGIN[a]) for a in range(3))]
            for lid in np.unique(local[local > 0]).tolist():
                g = offsets[row["tile"]] + lid
                target[local == lid] = object_of[g] if ids == "object" else g
        return volume

    def test_ids_and_offsets_round_trip(self):
        work = self.stitch(self.tiles_dir, decide_all("yes", overrides={(P000, P010): "no"}))
        err = self.export(work)
        self.assertIn("voxel size written: 30 x 8 x 8 nm (Z, Y, X)", err)
        layer, info = read_layer(self.out)
        scale = info["scales"][0]
        self.assertEqual((info["type"], info["data_type"], info["num_channels"]), ("segmentation", "uint64", 1))
        self.assertEqual(scale["size"], [64, 64, 20])                  # (x, y, z)
        self.assertEqual(scale["voxel_offset"], [32, 64, 96])          # the dataset origin, (x, y, z)
        self.assertEqual(scale["resolution"], [8, 8, 30])
        self.assertEqual((scale["encoding"], scale["chunk_sizes"]), ("compressed_segmentation", [[16, 16, 8]]))
        self.assertNotIn("sharding", scale)
        self.assertEqual(layer.dtype, np.uint64)
        np.testing.assert_array_equal(layer, self.expected(work))

        # Objects: A is one ID over three tiles, E one over the unequal Z seam, B stays two.
        a = self.object_of(work, P000, 1)
        self.assertEqual({self.object_of(work, P001, 1), self.object_of(work, P101, 7)}, {a})
        self.assertEqual(int(layer[98 - 96, 70 - 64, 56 - 32]), a)     # in P000
        self.assertEqual(int(layer[109 - 96, 73 - 64, 71 - 32]), a)    # in P101
        self.assertEqual(self.object_of(work, P000, 3), self.object_of(work, P100, 1))
        self.assertNotEqual(self.object_of(work, P000, 2), self.object_of(work, P010, 1))
        self.assertEqual(set(np.unique(layer).tolist()) - {0},
                         {int(r["object_id"]) for r in read_rows(work / "objects.csv")})

        # The layer's coordinates are the dataset's: boxes equal stitch_measure's.
        self.measure(work)
        for object_id, row in self.measurements(work).items():
            points = np.argwhere(layer == object_id) + ORIGIN
            box = tuple(v for axis in range(3) for v in (int(points[:, axis].min()), int(points[:, axis].max()) + 1))
            self.assertEqual(box, tuple(int(row[f"bbox_{a}{i}"]) for a in "zyx" for i in (0, 1)))
            self.assertEqual(len(points), int(row["voxel_count"]))

        run = json.loads((work / "export_run.json").read_text())
        self.assertEqual((run["ids"], run["voxel_size_zyx_nm"], run["voxel_offset_xyz"]),
                         ("object", [30.0, 8.0, 8.0], [32, 64, 96]))
        self.assertEqual(run["counts"]["segments"], len(set(np.unique(layer).tolist()) - {0}))
        self.assertEqual(run["counts"]["labelled_voxels"], int(np.count_nonzero(layer)))

    def test_missing_position_stores_no_chunks(self):
        work = self.stitch(self.tiles_dir, decide_all("yes"))
        self.export(work)
        layer, info = read_layer(self.out)
        self.assertFalse(layer[0:8, 32:64, 32:64].any())               # the missing position (P011)
        chunks = [p.name for p in (self.out / info["scales"][0]["key"]).iterdir()]
        self.assertLess(len(chunks), 4 * 4 * 3)                        # all-background chunks are not stored
        self.assertFalse([name for name in chunks if name.startswith(("64-80_96-", "80-96_96-"))
                          and name.endswith("_96-104")])

    def test_measure_volume_reads_the_export(self):
        # The export is a valid input for measure_volume: same IDs, voxel counts and boxes.
        work = self.stitch(self.tiles_dir, decide_all("yes"))
        self.export(work)
        self.measure(work)
        result = volume_measure.run_measure_volume(self.out, self.make_dir() / "mv", (30, 8, 8))
        stitched = self.measurements(work)
        self.assertEqual(sorted(m.label_id for m in result.measurements), sorted(stitched))
        for m in result.measurements:
            row = stitched[m.label_id]
            self.assertEqual(m.voxel_count, int(row["voxel_count"]))
            self.assertEqual(m.box, tuple(int(row[f"bbox_{a}{i}"]) for a in "zyx" for i in (0, 1)))
        self.assertTrue(result.run["voxel_size_matches_metadata"])     # the resolution written is the one given

    def test_label_ids_mode(self):
        work = self.stitch(self.tiles_dir, decide_all("yes"))
        self.export(work, "--ids", "label")
        layer, _ = read_layer(self.out)
        np.testing.assert_array_equal(layer, self.expected(work, ids="label"))
        self.assertEqual(set(np.unique(layer).tolist()) - {0},
                         {int(r["global_id"]) for r in read_rows(work / "id_map.csv")})

    def test_blocks_smaller_than_a_tile_give_the_same_layer(self):
        work = self.stitch(self.tiles_dir, decide_all("yes"))
        stitching_export.run_export(work, self.out, (30, 8, 8), chunk_xyz=(16, 16, 8), block_bytes=1)
        layer, _ = read_layer(self.out)
        np.testing.assert_array_equal(layer, self.expected(work))

    def test_tile_blocks_cover_the_tile_on_the_chunk_grid(self):
        tile = Tile("t", (100, 131, 70, 135, 0, 10))       # starts off the chunk grid in Z and Y
        blocks = list(stitching_export.tile_blocks(tile, (96, 200, 64, 200, 0, 10), (8, 16, 16), block_bytes=1))
        covered = np.zeros(tile.shape[:2], int)
        for z0, z1, y0, y1 in blocks:
            covered[z0:z1, y0:y1] += 1
            self.assertLessEqual(z1 - z0, 8)
            self.assertLessEqual(y1 - y0, 16)
        self.assertTrue((covered == 1).all())
        self.assertEqual(blocks[0], (0, 4, 0, 10))         # first cuts land on the grid: 104 and 80
        whole = list(stitching_export.tile_blocks(tile, (96, 200, 64, 200, 0, 10), (8, 16, 16)))
        self.assertEqual({(y0, y1) for _, _, y0, y1 in whole}, {(0, 10), (10, 65)})

    def test_refuses_bad_inputs_and_existing_folders(self):
        work = self.stitch(self.tiles_dir, decide_all("yes"))
        with self.assertRaisesRegex(CommandError, "needs region data"):
            self.export(work, "--region-only")
        self.export(work)
        with self.assertRaisesRegex(CommandError, "not empty; pass --overwrite"):
            self.export(work)
        self.export(work, "--overwrite")
        np.testing.assert_array_equal(read_layer(self.out)[0], self.expected(work))
        other = self.make_dir()
        (other / "notes.txt").write_text("keep me")
        with self.assertRaisesRegex(CommandError, "holds no precomputed 'info'"):
            self.call("stitch_export", "--work", str(work), "--out", str(other), *VOXEL_ARGS, "--overwrite")
        self.assertEqual((other / "notes.txt").read_text(), "keep me")
        # A tile changed after stitch_ids is refused, as in the other phases.
        path = Path(read_rows(work / "tiles.csv")[0]["path"])
        tifffile.imwrite(str(path), np.zeros((8, 32, 33), np.uint16), photometric="minisblack")
        with self.assertRaises(CommandError):
            self.export(work, "--overwrite")


@skipUnless(ts is not None, "tensorstore is not installed")
class ExportIdTests(StitchRunMixin, SimpleTestCase):
    def test_ids_beyond_float_precision_and_sparse_local_ids(self):
        # Local IDs near 2**63 skip the lookup table; the output IDs exceed 2**53 exactly.
        big = 2**63 + 5
        tiles_dir = self.make_dir()
        first = np.zeros((4, 8, 8), np.uint64)
        first[1:3, 2:6, 6:8] = big
        second = np.zeros((4, 8, 8), np.uint16)
        second[1:3, 2:6, 0:3] = 9
        tifffile.imwrite(str(tiles_dir / "0-4_0-8_0-8_b.tif"), first, photometric="minisblack")
        tifffile.imwrite(str(tiles_dir / "0-4_0-8_8-16_b.tif"), second, photometric="minisblack")
        work = self.stitch(tiles_dir, decide_all("no"))
        out = self.make_dir() / "layer"
        self.call("stitch_export", "--work", str(work), "--out", str(out), *VOXEL_ARGS)
        layer, _ = read_layer(out)
        self.assertEqual(set(np.unique(layer).tolist()), {0, big, big + 9})
        self.assertEqual(int(layer[1, 2, 7]), big)
        self.assertEqual(int(layer[1, 2, 8]), big + 9)

    def test_region_only_keeps_objects_reaching_the_region(self):
        tiles_dir = self.make_dir()
        names = ("0-4_0-8_0-8_r", "0-4_0-8_8-16_r")
        write_tiles(tiles_dir, names, (
            ("IN", (1, 3, 1, 3, 5, 11), {names[0]: 1, names[1]: 1}),    # crosses the seam; region only in tile 0
            ("OUT", (1, 3, 5, 7, 10, 14), {names[1]: 2}),               # entirely outside the region
        ))
        regions = self.make_dir()
        mask = np.zeros((4, 8, 8), np.uint8)
        mask[:, :, :6] = 1
        tifffile.imwrite(str(regions / f"{names[0]}_mask.tif"), mask, photometric="minisblack")
        work = self.make_dir() / "work"
        self.call("stitch_inventory", "--tiles", str(tiles_dir), "--work", str(work))
        self.call("stitch_ids", "--work", str(work), "--regions", str(regions))
        self.call("stitch_candidates", "--work", str(work))
        from annotation.test_stitching import write_decisions

        candidates = read_rows(work / "candidates.csv")
        path = write_decisions(work / "decisions.csv", candidates, {0: "yes"})
        self.call("stitch_objects", "--work", str(work), "--decisions", str(path))
        out = self.make_dir() / "layer"
        self.call("stitch_export", "--work", str(work), "--out", str(out), *VOXEL_ARGS, "--region-only")
        layer, _ = read_layer(out)
        merged = self.object_of(work, names[0], 1)
        self.assertEqual(set(np.unique(layer).tolist()), {0, merged})
        self.assertEqual(int(np.count_nonzero(layer)), 2 * 2 * 6)       # the whole object, also its outside part

        # The delivery README points at the layer; mito_id is the segment ID.
        self.call("stitch_measure", "--work", str(work), *VOXEL_ARGS, "--region-only")
        self.call("stitch_deliver", "--work", str(work))
        readme = (work / "delivery" / "README.md").read_text()
        self.assertIn("## Viewing in Neuroglancer", readme)
        self.assertIn(f"A layer was written to `{out.resolve()}`", readme)
        self.assertIn("only objects reaching into the region", readme)
        self.assertEqual([r["mito_id"] for r in read_rows(work / "delivery" / "mitochondria.csv")], [str(merged)])

    def test_readme_without_an_export(self):
        from annotation.stitching_deliver import readme_viewer_lines

        text = "\n".join(readme_viewer_lines(None))
        self.assertIn("No such layer had been written", text)
        # A layer of unmerged label IDs is not a view of the delivered objects.
        self.assertIn("No such layer", "\n".join(readme_viewer_lines({"ids": "label", "layer": "/x"})))
