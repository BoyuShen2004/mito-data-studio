"""measure_volume on synthetic Neuroglancer precomputed volumes.

The labels are written with tensorstore (compressed_segmentation, several
chunks, a voxel offset and deliberately wrong resolution metadata) next to a
region mask with a different chunk size. Every listed object is checked against
the per-volume code (``measure_label_volume``) run on the same in-memory
array with the same voxel size. No data is committed.
"""

import csv
import io
import json
import shutil
import tempfile
from pathlib import Path
from unittest import skipUnless

import numpy as np
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from annotation import stitching_deliver, volume_measure
from annotation.measurements import measure_label_volume
from annotation.stitching_measure import BBOX_FIELDS, CENTER_FIELDS
from annotation.visualization import slice_io

try:
    import tensorstore as ts
except ImportError:  # pragma: no cover - environment problem
    ts = None

VOXEL = (30.0, 8.0, 8.0)                 # (Z, Y, X) nm: the true size
WRONG_RESOLUTION_XYZ = [32, 32, 120]     # what the metadata claims (p7's mistake)
SHAPE = (24, 40, 48)                     # (Z, Y, X)
OFFSET = (10, 200, 100)                  # (Z, Y, X) voxel_offset
CHUNK_XYZ = [16, 16, 8]
REGION_Y = 28                            # region: local y < 28

A, B, C, D, E, F, H = 3_000_000_123, 2, 5, 7, 11, 13, 17
# (label, local (z0, z1, y0, y1, x0, x1)) painted in order.
OBJECTS = (
    (A, (6, 10, 12, 18, 4, 40)),    # crosses chunk faces on every axis, inside the region
    (B, (14, 18, 4, 8, 30, 48)),    # touches the +x face of the volume
    (C, (14, 20, 20, 36, 10, 16)),  # crosses the region boundary (y = 28)
    (D, (18, 22, 30, 38, 30, 40)),  # entirely outside the region
    (E, (1, 4, 20, 26, 20, 24)),    # E is an L: this arm and the next box
    (E, (1, 4, 20, 23, 20, 44)),
    (F, (0, 2, 2, 5, 2, 5)),        # dust (18 voxels) on the -z face: length 0
    (H, (4, 12, 32, 38, 2, 12)),    # outside the region; half of it is in the deleted chunk below
)
DELETED_CHUNK = (0, 8, 32, 40, 0, 16)   # local (Z, Y, X) box of a chunk file removed after writing


def write_precomputed(path: Path, array: np.ndarray, *, offset=OFFSET, resolution=WRONG_RESOLUTION_XYZ,
                      chunk=CHUNK_XYZ, data_type="uint32", encoding="compressed_segmentation") -> None:
    """Write a (Z, Y, X) array as a precomputed volume (all-zero chunks are not stored)."""
    scale = {"size": list(array.shape[::-1]), "encoding": encoding, "chunk_size": chunk,
             "resolution": resolution, "voxel_offset": list(offset[::-1])}
    if encoding == "compressed_segmentation":
        scale["compressed_segmentation_block_size"] = [8, 8, 8]
    store = ts.open({
        "driver": "neuroglancer_precomputed", "kvstore": {"driver": "file", "path": str(path) + "/"},
        "multiscale_metadata": {"type": "segmentation", "data_type": data_type, "num_channels": 1},
        "scale_metadata": scale, "create": True,
    }).result()
    store[...].write(array.transpose(2, 1, 0)[..., None]).result()


def chunk_file_name(local_box, offset=OFFSET) -> str:
    z0, z1, y0, y1, x0, x1 = (v + offset[i // 2] for i, v in enumerate(local_box))
    return f"{x0}-{x1}_{y0}-{y1}_{z0}-{z1}"


def read_rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def build_fixture(root: Path):
    labels = np.zeros(SHAPE, np.uint32)
    for lid, (z0, z1, y0, y1, x0, x1) in OBJECTS:
        labels[z0:z1, y0:y1, x0:x1] = lid
    write_precomputed(root / "labels", labels)
    chunk_file = root / "labels" / "32_32_120" / chunk_file_name(DELETED_CHUNK)
    chunk_file.unlink()
    z0, z1, y0, y1, x0, x1 = DELETED_CHUNK
    labels[z0:z1, y0:y1, x0:x1] = 0      # the reference sees the missing chunk as background
    region = np.zeros(SHAPE, np.uint8)
    region[:, :REGION_Y, :] = 1
    write_precomputed(root / "region", region, chunk=[32, 32, 8], data_type="uint8", encoding="raw")
    return labels


def reference_measurements(labels: np.ndarray, path: Path, **kwargs) -> dict:
    """``measure_label_volume`` on ``labels`` saved to ``path``, by label ID.

    The per-volume label scan (``find_objects``) costs memory and time in the
    largest label ID, so the reference runs on a compact relabelling (same
    masks, IDs 1..n) and maps the IDs back. Measurements depend only on each
    label's mask, not on its value."""
    ids = np.unique(labels)
    ids = ids[ids != 0]
    compact = np.searchsorted(ids, labels).astype(np.uint16) + 1
    compact[labels == 0] = 0
    np.save(path, compact)
    return {int(ids[m.label_id - 1]): m for m in measure_label_volume(path, VOXEL, **kwargs)}


@skipUnless(ts is not None, "tensorstore is not installed")
class MeasureVolumeTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = Path(tempfile.mkdtemp(prefix="mito-volume-"))
        cls.labels = build_fixture(cls.root)
        cls.reference = reference_measurements(cls.labels, cls.root / "reference.npy")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)
        slice_io.clear_caches()
        super().tearDownClass()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mito-volume-work-")
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)

    def run_command(self, *extra, region=True, labels=None):
        args = ["--labels", str(labels or self.root / "labels"), "--voxel-size-zyx", "30", "8", "8",
                "--work", str(self.work), *extra]
        if region:
            args += ["--region", str(self.root / "region")]
        err = io.StringIO()
        call_command("measure_volume", *args, stdout=io.StringIO(), stderr=err)
        return err.getvalue()

    def rows(self, name="mitochondria.csv"):
        return read_rows(self.work / "delivery" / name)

    def expected_box_and_center(self, lid):
        zz, yy, xx = np.nonzero(self.labels == lid)
        coords = [zz + OFFSET[0], yy + OFFSET[1], xx + OFFSET[2]]
        box = tuple(v for c in coords for v in (int(c.min()), int(c.max()) + 1))
        center = tuple(repr(int(c.sum()) / len(c) * VOXEL[a]) for a, c in enumerate(coords))
        return box, center

    # --- the key property --------------------------------------------------------

    def test_matches_the_per_volume_code(self):
        self.run_command()
        rows = {int(r["mito_id"]): r for r in self.rows()}
        self.assertEqual(sorted(rows), sorted(self.reference))       # no region-only: every label
        for lid, ref in self.reference.items():
            r = rows[lid]
            with self.subTest(label=lid):
                self.assertEqual(r["volume_um3"], repr(ref.volume_um3))
                self.assertAlmostEqual(float(r["length_um"]), ref.skeleton_length_um, delta=1e-9)
                self.assertEqual(r["cut_off_at_data_edge"], "true" if ref.touches_edge else "false")
                box, center = self.expected_box_and_center(lid)
                self.assertEqual(tuple(int(r[f]) for f in BBOX_FIELDS), box)
                self.assertEqual(tuple(r[f] for f in CENTER_FIELDS), center)
                self.assertEqual((r["n_tiles"], r["tiles"], r["touches_unproofread_tile"]), ("1", "labels", "false"))
        self.assertGreater(float(rows[A]["length_um"]), 0.2)
        self.assertEqual(float(rows[F]["length_um"]), 0.0)               # dust

    def test_flags(self):
        self.run_command()
        rows = {int(r["mito_id"]): r for r in self.rows()}
        flags = {lid: (r["cut_off_at_data_edge"], r["crosses_region_boundary"], r["core"], r["not_core_reasons"])
                 for lid, r in rows.items()}
        self.assertEqual(flags, {
            A: ("false", "false", "true", ""),
            B: ("true", "false", "false", "cut_off_at_data_edge"),
            C: ("false", "true", "false", "crosses_region_boundary"),
            D: ("false", "false", "true", ""),
            E: ("false", "false", "true", ""),
            F: ("true", "false", "false", "cut_off_at_data_edge"),
            H: ("false", "false", "true", ""),
        })
        self.assertEqual({lid: r["frac_in_region"] for lid, r in rows.items() if lid in (C, D, A)},
                         {A: "1.0", C: repr(8 * 6 * 6 / (16 * 6 * 6)), D: "0.0"})
        self.assertEqual(rows[C]["volume_in_region_um3"], repr(8 * 6 * 6 * 1920.0 / 1e9))

    def test_region_only(self):
        err = self.run_command("--region-only")
        self.assertIn("5 mitochondria (2 core) of 7 label(s), 0 excluded", err)
        self.assertEqual([r["mito_id"] for r in self.rows()], [str(v) for v in sorted((A, B, C, E, F))])

    def test_missing_chunks_are_background(self):
        self.run_command()
        stats = {int(r["label_id"]): r for r in read_rows(self.work / "label_stats.csv")}
        self.assertEqual(int(stats[H]["voxel_count"]), 4 * 6 * 10)    # only the part outside the deleted chunk
        self.assertEqual(int(stats[H]["bbox_z0"]), 8 + OFFSET[0])
        run = json.loads((self.work / "measure_run.json").read_text())
        files = [p for p in (self.root / "labels" / "32_32_120").iterdir()]
        self.assertEqual(run["counts"]["chunk_files"], len(files))
        self.assertLess(len(files), 3 * 3 * 3)                         # empty chunks are not stored

    def test_crop_limit(self):
        self.run_command("--max-crop-voxels", "1,000")
        reference = reference_measurements(self.labels, self.work.parent / f"{self.work.name}-ref.npy",
                                           max_crop_voxels=1000)
        self.addCleanup((self.work.parent / f"{self.work.name}-ref.npy").unlink)
        rows = {int(r["mito_id"]): r for r in self.rows()}
        skipped = {lid for lid, m in reference.items() if m.skipped_reason}
        self.assertTrue(skipped and skipped != set(reference))
        self.assertEqual({lid for lid, r in rows.items() if r["length_status"] == "skipped_too_large"}, skipped)
        for lid in skipped:
            with self.subTest(label=lid):
                self.assertEqual((rows[lid]["length_um"], rows[lid]["volume_um3"]),
                                 ("", repr(reference[lid].volume_um3)))
                self.assertEqual(tuple(rows[lid][f] for f in CENTER_FIELDS), self.expected_box_and_center(lid)[1])
        summary = {r["metric"]: r["all"] for r in self.rows("summary.csv")}
        self.assertEqual(summary["length_skipped_too_large"], str(len(skipped)))

    def test_exclusions(self):
        listing = self.work.parent / f"{self.work.name}-not_mito.txt"
        self.addCleanup(listing.unlink, missing_ok=True)
        listing.write_text(f"label_id\n# reviewer\n{C}\n\n{D}  # outside\n{C}\n")
        err = self.run_command("--exclude-label-ids", str(listing), "--region-only")
        self.assertIn("4 mitochondria (2 core) of 7 label(s), 2 excluded", err)
        self.assertNotIn(str(C), [r["mito_id"] for r in self.rows()])
        excluded = self.rows("excluded.csv")
        self.assertEqual([(r["mito_id"], r["excluded_global_ids"], r["n_tiles"], r["tiles"], r["reaches_region"])
                          for r in excluded], [(str(C), str(C), "1", "labels", "true"),
                                               (str(D), str(D), "1", "labels", "false")])
        self.assertEqual(excluded[0]["volume_um3"], repr(16 * 6 * 6 * 1920.0 / 1e9))

    def test_bad_exclusions_are_refused_before_reading(self):
        listing = self.work.parent / f"{self.work.name}-bad.txt"
        self.addCleanup(listing.unlink, missing_ok=True)
        listing.write_text("abc\n")
        with self.assertRaisesMessage(CommandError, "'abc' is not a label id"):
            self.run_command("--exclude-label-ids", str(listing))
        self.assertFalse((self.work / "label_stats.csv").exists())
        listing.write_text(f"{A}\n999\n")
        with self.assertRaisesMessage(CommandError, ":2: label id 999 is not in the volume"):
            self.run_command("--exclude-label-ids", str(listing))
        self.assertFalse((self.work / "delivery").exists())

    def test_voxel_size_override_is_recorded(self):
        err = self.run_command("--region-only")
        self.assertIn("voxel size override recorded", err)
        run = json.loads((self.work / "measure_run.json").read_text())
        self.assertEqual(run["voxel_size_zyx_nm"], [30.0, 8.0, 8.0])
        self.assertEqual(run["labels"]["metadata_resolution_zyx_nm"], [120.0, 32.0, 32.0])
        self.assertIs(run["voxel_size_matches_metadata"], False)
        self.assertIn("overrides the labels' metadata resolution [120.0, 32.0, 32.0]", run["voxel_size_note"])
        self.assertEqual((run["region_only"], run["max_crop_voxels"], run["labels"]["voxel_offset_zyx"]),
                         (True, 512_000_000, list(OFFSET)))
        self.assertEqual(run["labels"]["path"], str((self.root / "labels").resolve()))
        self.assertIn("--region-only", run["command"])
        readme = (self.work / "delivery" / "README.md").read_text()
        self.assertIn("**Voxel size override.** The volume's metadata says 120 × 32 × 32 nm (Z, Y, X)", readme)

    def test_matching_metadata(self):
        labels = self.work / "matching"
        write_precomputed(labels, self.labels, resolution=[8, 8, 30])
        self.run_command(region=False, labels=labels)
        run = json.loads((self.work / "measure_run.json").read_text())
        self.assertIs(run["voxel_size_matches_metadata"], True)
        self.assertIn("The given voxel size matches the volume's metadata",
                      (self.work / "delivery" / "README.md").read_text())

    def test_without_region(self):
        self.run_command(region=False)
        rows = self.rows()
        self.assertEqual(len(rows), len(self.reference))
        self.assertEqual({(r["volume_in_region_um3"], r["frac_in_region"], r["crosses_region_boundary"])
                          for r in rows}, {("", "", "false")})
        self.assertNotIn("region_voxels", read_rows(self.work / "label_stats.csv")[0])
        with self.assertRaisesMessage(CommandError, "--region-only needs --region"):
            self.run_command("--region-only", "--overwrite", region=False)

    def test_columns_match_stitch_deliver(self):
        listing = self.work.parent / f"{self.work.name}-x.txt"
        self.addCleanup(listing.unlink, missing_ok=True)
        listing.write_text(f"{D}\n")
        self.run_command("--exclude-label-ids", str(listing))
        self.assertEqual(list(self.rows()[0]), list(stitching_deliver.MITO_FIELDS))
        self.assertEqual(list(self.rows("excluded.csv")[0]), list(stitching_deliver.EXCLUDED_FIELDS))
        summary = self.rows("summary.csv")
        self.assertEqual(list(summary[0]), list(stitching_deliver.SUMMARY_FIELDS))
        self.assertEqual([r["metric"] for r in summary], list(stitching_deliver.SUMMARY_METRICS))
        self.assertEqual(sorted(p.name for p in (self.work / "delivery").iterdir()),
                         sorted(stitching_deliver.DELIVERY_FILES))

    def test_readme(self):
        self.run_command("--region-only", "--volume-name", "p7")
        readme = (self.work / "delivery" / "README.md").read_text()
        for name in (*stitching_deliver.MITO_FIELDS, *stitching_deliver.SUMMARY_METRICS,
                     *stitching_deliver.EXCLUDED_FIELDS):
            with self.subTest(column=name):
                self.assertIn(f"`{name}`", readme)
        for text in ("# Mitochondria: measurements of `p7`", "stitched and seam-proofread upstream",
                     "30 × 8 × 8 nm (Z, Y, X)", "48 × 40 × 24 voxels (x, y, z)", "TEASAR", "cable length",
                     "Scope: region only", f"`{(self.root / 'region').resolve()}`",
                     f"`{(self.root / 'labels').resolve()}`", "code revision `", "command `measure_volume --labels",
                     "## Constant columns", "including its voxel offset [10, 200, 100]",
                     f"tensorstore {volume_measure.tensorstore_version()}"):
            with self.subTest(text=text):
                self.assertIn(text, readme)
        self.assertEqual({r["tiles"] for r in self.rows()}, {"p7"})

    # --- refusals -----------------------------------------------------------------

    def test_region_on_another_grid_is_refused(self):
        region = self.work / "small_region"
        write_precomputed(region, np.ones((24, 40, 40), np.uint8), data_type="uint8", encoding="raw")
        with self.assertRaisesMessage(CommandError, "region grid does not match the labels"):
            call_command("measure_volume", "--labels", str(self.root / "labels"), "--region", str(region),
                         "--voxel-size-zyx", "30", "8", "8", "--work", str(self.work), stderr=io.StringIO())
        shifted = self.work / "shifted_region"
        write_precomputed(shifted, np.ones(SHAPE, np.uint8), offset=(10, 200, 0), data_type="uint8",
                          encoding="raw")
        with self.assertRaisesMessage(CommandError, "region grid does not match the labels"):
            call_command("measure_volume", "--labels", str(self.root / "labels"), "--region", str(shifted),
                         "--voxel-size-zyx", "30", "8", "8", "--work", str(self.work), stderr=io.StringIO())

    def test_existing_delivery_needs_overwrite(self):
        self.run_command(region=False)
        with self.assertRaisesMessage(CommandError, "is not empty; pass --overwrite"):
            self.run_command(region=False)
        self.run_command("--overwrite", region=False)

    def test_unexpected_files_and_sharding_are_refused(self):
        labels = self.work / "copy"
        shutil.copytree(self.root / "labels", labels)
        (labels / "32_32_120" / f"{chunk_file_name((8, 16, 0, 16, 0, 16))}.gz").write_bytes(b"")
        with self.assertRaisesMessage(CommandError, "unexpected files"):
            self.run_command(labels=labels, region=False)
        info = json.loads((labels / "info").read_text())
        info["scales"][0]["sharding"] = {"@type": "neuroglancer_uint64_sharded_v1"}
        (labels / "info").write_text(json.dumps(info))
        with self.assertRaisesMessage(CommandError, "scale 0 is sharded"):
            self.run_command(labels=labels, region=False)


class ChunkStatsTests(SimpleTestCase):
    def test_chunk_without_background(self):
        labels = np.array([[[4, 4], [9, 9]], [[4, 9], [9, 9]]], np.uint64)
        region = np.array([[[1, 0], [0, 0]], [[0, 0], [0, 1]]], np.uint8)
        self.assertEqual(volume_measure.chunk_label_stats(labels, region), [
            (4, 3, 1, (0, 2, 0, 1, 0, 2)),
            (9, 5, 1, (0, 2, 0, 2, 0, 2)),
        ])

    def test_large_ids_and_background(self):
        labels = np.zeros((3, 4, 5), np.uint64)
        labels[1, 2:4, 3] = 2**63 + 5
        self.assertEqual(volume_measure.chunk_label_stats(labels, None), [(2**63 + 5, 2, 0, (1, 2, 2, 4, 3, 4))])
