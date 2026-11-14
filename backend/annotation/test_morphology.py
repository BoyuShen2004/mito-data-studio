"""Morphology features on synthetic shapes with known answers.

Shapes are analytic solids sampled on anisotropic (30, 8, 8) nm voxels, placed
off the voxel grid on purpose. The stitched-tile and ``measure_volume`` paths
are checked against ``measure_morphology`` on the same object in one unsplit
array. No data is committed.
"""

import math
import shutil
import tempfile
from pathlib import Path
from unittest import skipUnless

import numpy as np
import scipy.ndimage as ndi
from django.test import SimpleTestCase

from annotation import stitching_deliver, stitching_measure
from annotation.measurements import CROP_PAD, skeleton_length_nm, skeletonize_mask
from annotation.morphology import (
    MORPHOLOGY_FIELDS,
    Morphology,
    longest_path_nm,
    measure_morphology,
    skeleton_radii_nm,
    sphericity,
    width_stats_nm,
)
from annotation.test_stitching import StitchRunMixin, decide_all, read_rows, write_tiles
from annotation.test_volume_measure import write_precomputed
from annotation.visualization import slice_io

try:
    import tensorstore as ts
except ImportError:  # pragma: no cover - environment problem
    ts = None

VOXEL = (30.0, 8.0, 8.0)     # (Z, Y, X) nm
RADIUS, LENGTH = 120.0, 1600.0
# The 22 mitochondria.csv columns of the deliveries made before morphology.
ORIGINAL_MITO_FIELDS = (
    "mito_id", "volume_um3", "length_um", "volume_in_region_um3", "frac_in_region", "n_tiles", "tiles",
    "cut_off_at_data_edge", "crosses_region_boundary", "touches_unproofread_tile", "length_status",
    "bbox_z0", "bbox_z1", "bbox_y0", "bbox_y1", "bbox_x0", "bbox_x1", "center_z_nm", "center_y_nm", "center_x_nm",
    "core", "not_core_reasons")


def grid_nm(shape):
    """Voxel-centre coordinates (nm) of a (Z, Y, X) array, as open grids."""
    z, y, x = np.ogrid[:shape[0], :shape[1], :shape[2]]
    return z * VOXEL[0], y * VOXEL[1], x * VOXEL[2]


def cylinder_x(radius=RADIUS, length=LENGTH, offset=(7.0, 2.0), pad=4):
    """A flat-ended cylinder along X, its axis ``offset`` nm off the grid centre."""
    shape = (int(2 * radius / VOXEL[0]) + 2 * pad, int(2 * radius / VOXEL[1]) + 2 * pad,
             int(length / VOXEL[2]) + 2 * pad)
    z, y, x = grid_nm(shape)
    cz, cy = (shape[0] - 1) / 2 * VOXEL[0] + offset[0], (shape[1] - 1) / 2 * VOXEL[1] + offset[1]
    return ((z - cz) ** 2 + (y - cy) ** 2 <= radius ** 2) & (x >= pad * VOXEL[2]) & (x < pad * VOXEL[2] + length)


def capsule_x(radius=RADIUS, length=LENGTH, offset=(7.0, 2.0), pad=4):
    """A cylinder along X with hemispherical ends (``length`` is the straight part)."""
    shape = (int(2 * radius / VOXEL[0]) + 2 * pad, int(2 * radius / VOXEL[1]) + 2 * pad,
             int((length + 2 * radius) / VOXEL[2]) + 2 * pad)
    z, y, x = grid_nm(shape)
    cz, cy = (shape[0] - 1) / 2 * VOXEL[0] + offset[0], (shape[1] - 1) / 2 * VOXEL[1] + offset[1]
    start = pad * VOXEL[2] + radius
    return (z - cz) ** 2 + (y - cy) ** 2 + (x - np.clip(x, start, start + length)) ** 2 <= radius ** 2


def cylinder_z(radius=RADIUS, length=1500.0, offset=(3.0, 2.0), pad=4):
    shape = (int(length / VOXEL[0]) + 2 * pad, int(2 * radius / VOXEL[1]) + 2 * pad,
             int(2 * radius / VOXEL[2]) + 2 * pad)
    z, y, x = grid_nm(shape)
    cy, cx = (shape[1] - 1) / 2 * VOXEL[1] + offset[0], (shape[2] - 1) / 2 * VOXEL[2] + offset[1]
    return ((y - cy) ** 2 + (x - cx) ** 2 <= radius ** 2) & (z >= pad * VOXEL[0]) & (z < pad * VOXEL[0] + length)


def sphere(radius=300.0, pad=4):
    shape = (int(2 * radius / VOXEL[0]) + 2 * pad, int(2 * radius / VOXEL[1]) + 2 * pad,
             int(2 * radius / VOXEL[2]) + 2 * pad)
    z, y, x = grid_nm(shape)
    centre = [(n - 1) / 2 * v for n, v in zip(shape, VOXEL)]
    return (z - centre[0]) ** 2 + (y - centre[1]) ** 2 + (x - centre[2]) ** 2 <= radius ** 2


def y_shape(radius=80.0, arm=800.0):
    """Three round arms in the XY plane meeting at one point, 120 degrees apart."""
    half = arm + 2 * radius
    shape = (int(2 * radius / VOXEL[0]) + 8, int(2 * half / VOXEL[1]) + 8, int(2 * half / VOXEL[2]) + 8)
    z, y, x = grid_nm(shape)
    centre = np.array([(n - 1) / 2 * v for n, v in zip(shape, VOXEL)]) + (7.0, 3.0, 2.0)
    mask = np.zeros(shape, bool)
    for angle in (90.0, 210.0, 330.0):
        dy, dx = math.sin(math.radians(angle)), math.cos(math.radians(angle))
        along = np.clip((y - centre[1]) * dy + (x - centre[2]) * dx, 0.0, arm)    # nearest point on the arm's axis
        mask |= ((z - centre[0]) ** 2 + (y - centre[1] - along * dy) ** 2
                 + (x - centre[2] - along * dx) ** 2 <= radius ** 2)
    return mask


class WidthTests(SimpleTestCase):
    def test_unrefined_radius_is_the_anisotropic_distance_transform(self):
        mask = cylinder_x()
        skeleton = skeletonize_mask(mask, VOXEL)
        index = np.rint(np.asarray(skeleton.vertices) / VOXEL).astype(int)
        # Vertices are voxel centres in nm: voxel size was applied once, by vol_to_skel.
        np.testing.assert_allclose(index * VOXEL, skeleton.vertices)
        self.assertTrue(mask[tuple(index.T)].all())
        # Everything outside the array is background, hence the padding.
        edt = ndi.distance_transform_edt(np.pad(mask, 1), sampling=VOXEL)[tuple((index + 1).T)]
        radii = skeleton_radii_nm(mask, skeleton.vertices, VOXEL, refine=False)
        np.testing.assert_allclose(radii, edt, rtol=1e-9)
        refined = skeleton_radii_nm(mask, skeleton.vertices, VOXEL)
        self.assertTrue((refined >= radii).all())
        self.assertLessEqual(float((refined - radii).max()), VOXEL[0] / 2 + 1e-6)

    def test_cylinder_width_along_x(self):
        for offset in ((7.0, 2.0), (15.0, 4.0), (0.0, 0.0)):   # (15, 4) and (0, 0): axis between voxel centres
            with self.subTest(offset=offset):
                m = measure_morphology(cylinder_x(offset=offset), VOXEL)
                # Half a Z voxel is the accuracy the sampling allows.
                self.assertAlmostEqual(m.width_median_nm, 2 * RADIUS, delta=VOXEL[0] / 2)
                self.assertAlmostEqual(m.width_max_nm, 2 * RADIUS, delta=VOXEL[0] / 2)
                self.assertLessEqual(m.width_min_nm, m.width_median_nm)

    def test_capsule_width(self):
        # Rounded ends, as real mitochondria have: the tips are left out, so the mean is right too.
        # (In a flat-ended cylinder the skeleton dives into the corners of the end faces.)
        m = measure_morphology(capsule_x(), VOXEL)
        self.assertAlmostEqual(m.width_median_nm, 2 * RADIUS, delta=VOXEL[0] / 2)
        self.assertAlmostEqual(m.width_mean_nm, 2 * RADIUS, delta=VOXEL[0] / 2)
        self.assertAlmostEqual(m.width_max_nm, 2 * RADIUS, delta=VOXEL[0] / 2)
        # The skeleton stops a little short of the rounded tips.
        self.assertAlmostEqual(m.end_to_end_nm, LENGTH + 2 * RADIUS, delta=0.05 * (LENGTH + 2 * RADIUS))
        area = 2 * math.pi * RADIUS * LENGTH + 4 * math.pi * RADIUS ** 2
        self.assertAlmostEqual(m.surface_area_nm2, area, delta=0.04 * area)

    def test_cylinder_width_along_z(self):
        m = measure_morphology(cylinder_z(), VOXEL)
        self.assertAlmostEqual(m.width_median_nm, 2 * RADIUS, delta=VOXEL[1])
        self.assertAlmostEqual(m.width_max_nm, 2 * RADIUS, delta=VOXEL[1])
        self.assertAlmostEqual(m.end_to_end_nm, 1500.0, delta=0.1 * 1500.0)

    def test_tips_are_left_out_and_weights_follow_cable(self):
        # A straight path along X: radius 100 in the middle, tapering to 0 at both ends.
        x = np.arange(0.0, 1001.0, 10.0)
        vertices = np.stack([np.zeros_like(x), np.zeros_like(x), x], axis=1)
        edges = np.stack([np.arange(100), np.arange(1, 101)], axis=1)
        radii = np.minimum(100.0, np.minimum(x, 1000.0 - x))
        mean, median, low, high = width_stats_nm(radii, vertices, edges)
        self.assertEqual((mean, median, low, high), (200.0, 200.0, 200.0, 200.0))
        # A blob: every vertex is within 1.5 radii of an end, so all are used.
        mean, median, low, high = width_stats_nm(np.full(101, 2000.0), vertices, edges)
        self.assertEqual((low, high), (4000.0, 4000.0))
        # The maximum is over every vertex, also the ones left out as tips.
        peak = radii.copy()
        peak[5] = 400.0
        self.assertEqual(width_stats_nm(peak, vertices, edges)[2:], (200.0, 800.0))
        # Sparse sampling: one long edge and many short ones; the mean follows cable, not vertex count.
        vertices = np.array([[0, 0, 0], [0, 0, 900], *[[0, 0, 900 + 10 * i] for i in range(1, 11)]], float)
        edges = np.stack([np.arange(11), np.arange(1, 12)], axis=1)
        radii = np.array([5.0, 5.0, *[15.0] * 10])
        mean, median, _, _ = width_stats_nm(radii, vertices, edges)
        self.assertLess(mean, 20.0)       # unweighted would be 2 * (2 * 5 + 10 * 15) / 12 = 26.7
        self.assertEqual(median, 10.0)


class ShapeTests(SimpleTestCase):
    def test_cylinder(self):
        mask = cylinder_x()
        m = measure_morphology(mask, VOXEL)
        self.assertEqual(m.length_nm, skeleton_length_nm(mask, VOXEL))    # the same skeleton as the cable length
        self.assertEqual((m.branch_points, m.endpoints), (0, 2))
        # The skeleton ends in the corners of the flat end faces.
        self.assertAlmostEqual(m.end_to_end_nm, LENGTH, delta=0.05 * LENGTH)
        self.assertAlmostEqual(m.longest_path_nm, m.length_nm, delta=1e-6 * m.length_nm)
        self.assertEqual(m.tortuosity, m.length_nm / m.end_to_end_nm)
        self.assertGreaterEqual(m.tortuosity, 1.0)
        area = 2 * math.pi * RADIUS * LENGTH + 2 * math.pi * RADIUS ** 2
        self.assertAlmostEqual(m.surface_area_nm2, area, delta=0.04 * area)
        expected = sphericity(math.pi * RADIUS ** 2 * LENGTH, area)       # 0.65
        self.assertAlmostEqual(m.sphericity, expected, delta=0.03)

    def test_sphere(self):
        radius = 300.0
        m = measure_morphology(sphere(radius), VOXEL)
        area = 4 * math.pi * radius ** 2
        self.assertAlmostEqual(m.surface_area_nm2, area, delta=0.04 * area)
        self.assertAlmostEqual(m.sphericity, 1.0, delta=0.04)
        self.assertAlmostEqual(m.width_max_nm, 2 * radius, delta=VOXEL[0])
        self.assertEqual(m.branch_points, 0)

    def test_y_branch(self):
        mask = y_shape()
        m = measure_morphology(mask, VOXEL)
        self.assertEqual((m.branch_points, m.endpoints), (1, 3))
        self.assertAlmostEqual(m.width_median_nm, 160.0, delta=VOXEL[0] / 2)
        # The longest path is two of the three arms; the ends are 120 degrees apart.
        self.assertLess(m.longest_path_nm, 0.8 * m.length_nm)
        self.assertAlmostEqual(m.end_to_end_nm, 2 * 880.0 * math.sin(math.radians(60)), delta=120.0)
        self.assertEqual(m.tortuosity, m.length_nm / m.end_to_end_nm)
        self.assertGreater(m.tortuosity, 1.4)       # three arms of cable over a two-arm chord

    def test_longest_path_over_a_forest(self):
        # Two pieces: an L of 30 + 40 (ends 50 apart) and a straight 60.
        vertices = np.array([[0, 0, 0], [0, 0, 30], [0, 40, 30], [9, 0, 0], [9, 0, 60], [0, 0, 15]], float)
        edges = np.array([[0, 5], [5, 1], [1, 2], [3, 4]])
        self.assertEqual(longest_path_nm(vertices, edges), (70.0, 50.0))
        self.assertEqual(longest_path_nm(vertices[:1], np.zeros((0, 2), int)), (0.0, 0.0))

    def test_empty_and_dust(self):
        self.assertEqual(measure_morphology(np.zeros((6, 6, 6), bool), VOXEL), Morphology())
        dust = np.zeros((10, 10, 10), bool)
        dust[2:5, 2:5, 2:5] = True
        m = measure_morphology(dust, VOXEL)
        self.assertEqual((m.length_nm, m.width_median_nm, m.branch_points, m.tortuosity), (0.0, None, None, None))
        self.assertGreater(m.surface_area_nm2, 0)
        row = m.row()
        self.assertEqual(tuple(row), MORPHOLOGY_FIELDS)
        self.assertEqual((row["width_mean_nm"], row["endpoints"], row["tortuosity"]), ("", "", ""))
        self.assertEqual(row["surface_area_um2"], repr(m.surface_area_nm2 / 1e6))


SEAM_TILES_X = ("0-16_0-40_0-64_s", "0-16_0-40_64-128_s")
SEAM_TILES_Z = ("0-24_0-40_0-40_s", "24-48_0-40_0-40_s")


def write_mask_tiles(directory: Path, names, mask: np.ndarray) -> None:
    """Split ``mask`` (the whole region) into the tiles ``names``, label 1 in each."""
    import tifffile

    from annotation.stitching import parse_tile_name

    for name in names:
        _, box = parse_tile_name(name)
        part = mask[tuple(slice(box[2 * a], box[2 * a + 1]) for a in range(3))]
        tifffile.imwrite(str(directory / f"{name}.tif"), part.astype(np.uint16), photometric="minisblack")


def region_cylinder(shape, axis: int, radius_nm: float, margin: int):
    """A cylinder along ``axis`` through the middle of a region of ``shape``,
    stopping ``margin`` voxels short of both ends."""
    grids = grid_nm(shape)
    others = [a for a in range(3) if a != axis]
    centre = [(shape[a] - 1) / 2 * VOXEL[a] + 3.0 for a in others]
    index = np.ogrid[:shape[0], :shape[1], :shape[2]][axis]
    return (((grids[others[0]] - centre[0]) ** 2 + (grids[others[1]] - centre[1]) ** 2 <= radius_nm ** 2)
            & (index >= margin) & (index < shape[axis] - margin))


def expected_morphology(mask: np.ndarray) -> Morphology:
    """``measure_morphology`` on the unsplit object, cropped by the per-volume rule."""
    box = ndi.find_objects(mask.astype(np.uint8))[0]
    crop = tuple(slice(max(0, s.start - CROP_PAD), min(n, s.stop + CROP_PAD)) for s, n in zip(box, mask.shape))
    return measure_morphology(mask[crop], VOXEL)


class SeamSplitTests(StitchRunMixin, SimpleTestCase):
    """A cylinder split across a seam measures exactly as the unsplit one."""

    def check(self, names, shape, axis, radius_nm):
        mask = region_cylinder(shape, axis, radius_nm, margin=4)
        tiles_dir = self.make_dir()
        write_mask_tiles(tiles_dir, names, mask)
        work = self.stitch(tiles_dir, decide_all("yes"))
        self.call("stitch_measure", "--work", str(work), "--voxel-size-zyx", "30", "8", "8")
        (row,) = read_rows(work / "measurements.csv")
        self.assertEqual((row["member_count"], row["touches_edge"]), ("2", "false"))
        expected = expected_morphology(mask)
        self.assertEqual({f: row[f] for f in MORPHOLOGY_FIELDS}, expected.row())
        self.assertEqual(row["skeleton_length_um"], repr(expected.length_nm / 1000.0))
        self.assertEqual((expected.branch_points, expected.endpoints), (0, 2))
        self.assertAlmostEqual(expected.width_median_nm, 2 * radius_nm, delta=VOXEL[0] / 2)
        return work, row

    def test_x_seam(self):
        self.check(SEAM_TILES_X, (16, 40, 128), 2, 100.0)

    def test_z_seam(self):
        self.check(SEAM_TILES_Z, (48, 40, 40), 0, 100.0)

    def test_unmerged_halves_differ_from_the_whole(self):
        mask = region_cylinder((16, 40, 128), 2, 100.0, margin=4)
        tiles_dir = self.make_dir()
        write_mask_tiles(tiles_dir, SEAM_TILES_X, mask)
        work = self.stitch(tiles_dir, decide_all("no"))
        self.call("stitch_measure", "--work", str(work), "--voxel-size-zyx", "30", "8", "8")
        rows = read_rows(work / "measurements.csv")
        whole = expected_morphology(mask)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertLess(float(row["end_to_end_um"]), 0.6 * whole.end_to_end_nm / 1000.0)
            self.assertAlmostEqual(float(row["width_median_nm"]), whole.width_median_nm, delta=VOXEL[0] / 2)

    def test_crop_limit_leaves_morphology_empty(self):
        mask = region_cylinder((16, 40, 128), 2, 100.0, margin=4)
        tiles_dir = self.make_dir()
        write_mask_tiles(tiles_dir, SEAM_TILES_X, mask)
        work = self.stitch(tiles_dir, decide_all("yes"))
        self.call("stitch_measure", "--work", str(work), "--voxel-size-zyx", "30", "8", "8",
                  "--max-crop-voxels", "1000")
        (row,) = read_rows(work / "measurements.csv")
        self.assertEqual(row["skipped_reason"], "crop_limit")
        self.assertEqual({row[f] for f in MORPHOLOGY_FIELDS}, {""})

    def test_measurements_columns(self):
        fields = stitching_measure.MEASUREMENTS_FIELDS
        self.assertEqual(fields[-len(MORPHOLOGY_FIELDS):], MORPHOLOGY_FIELDS)
        self.assertEqual(fields.index("center_x_nm") + 1, len(fields) - len(MORPHOLOGY_FIELDS))


class DeliveryColumnTests(SimpleTestCase):
    def test_new_columns_are_appended_to_the_original_ones(self):
        self.assertEqual(stitching_deliver.MITO_FIELDS, (*ORIGINAL_MITO_FIELDS, *MORPHOLOGY_FIELDS))

    def test_old_measurements_rows_deliver_with_empty_morphology(self):
        old = {"object_id": "5", "volume_um3": "0.1", "skeleton_length_um": "1.5", "skipped_reason": "",
               "touches_edge": "false", "crosses_region_boundary": "false", "unproofread_tiles": "0",
               "unresolved_contacts": "0", "tiles": "t", "region_voxels": "10", "frac_in_region": "1.0",
               **dict.fromkeys(stitching_measure.BBOX_FIELDS, "0"), **dict.fromkeys(stitching_measure.CENTER_FIELDS, "0.0")}
        row, _ = stitching_deliver.mito_row(old, 1920.0)
        self.assertEqual(tuple(row), stitching_deliver.MITO_FIELDS)
        self.assertEqual({row[f] for f in MORPHOLOGY_FIELDS}, {""})
        self.assertEqual(row["core"], "true")

    def test_readme_documents_every_morphology_column(self):
        docs = stitching_deliver._column_docs(VOXEL)
        self.assertEqual(tuple(docs), stitching_deliver.MITO_FIELDS)
        text = "\n".join(stitching_deliver.readme_length_lines("Crop sentence.", 1000))
        self.assertIn("## How width and shape are measured", text)
        self.assertIn("Euclidean distance", text)


@skipUnless(ts is not None, "tensorstore is not installed")
class MeasureVolumeMorphologyTests(SimpleTestCase):
    def test_volume_rows_match_the_unsplit_mask(self):
        root = Path(tempfile.mkdtemp(prefix="mito-morph-"))
        self.addCleanup(shutil.rmtree, root, True)
        self.addCleanup(slice_io.clear_caches)
        shape = (16, 40, 128)
        cylinder = region_cylinder(shape, 2, 100.0, margin=4)
        labels = np.zeros(shape, np.uint32)
        labels[cylinder] = 9
        labels[1:3, 1:4, 1:4] = 4                      # dust: no skeleton
        write_precomputed(root / "labels", labels, offset=(5, 6, 7), chunk=[32, 16, 8])
        from annotation.volume_measure import run_measure_volume

        logs = []
        result = run_measure_volume(root / "labels", root / "work", VOXEL, log=logs.append)
        rows = {int(r["mito_id"]): r for r in read_rows(result.out / "mitochondria.csv")}
        self.assertEqual(list(next(iter(rows.values()))), list(stitching_deliver.MITO_FIELDS))
        expected = expected_morphology(cylinder)
        self.assertEqual({f: rows[9][f] for f in MORPHOLOGY_FIELDS}, expected.row())
        self.assertEqual(rows[9]["length_um"], repr(expected.length_nm / 1000.0))
        self.assertEqual(rows[4]["width_median_nm"], "")
        self.assertNotEqual(rows[4]["surface_area_um2"], "")
        self.assertIn("How width and shape are measured", (result.out / "README.md").read_text())
        # The resolution used is always reported (the metadata here is wrong on purpose).
        self.assertTrue(any("voxel size" in line for line in logs))
