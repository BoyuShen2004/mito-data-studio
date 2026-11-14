"""Stitching re-runs: carrying review decisions, and tiles of different sizes.

Each test stitches a small synthetic tile set, changes it the way real data
changes (a missing slab is filled, a tile is relabelled, a tile is removed),
re-runs phases 1-3 in a new work directory and carries the earlier decisions.
No data is committed.
"""

import json
import shutil
from pathlib import Path

import numpy as np
import tifffile
from django.core.management import CommandError
from django.test import SimpleTestCase

from annotation import stitching, stitching_rerun
from annotation.stitching import parse_tile_name
from annotation.test_stitching import (
    StitchRunMixin,
    _write_tiff,
    as_tuples,
    em_value,
    read_rows,
    write_decisions,
    write_tiles,
)

VOXEL_ARGS = ("--voxel-size-zyx", "30", "8", "8")

# Two layers of two tiles with the Z slab 8-16 missing, then the slab itself.
LA, LB = "0-8_0-32_0-32_s", "0-8_0-32_32-64_s"
MA, MB = "8-16_0-32_0-32_s", "8-16_0-32_32-64_s"
UA, UB = "16-24_0-32_0-32_s", "16-24_0-32_32-64_s"
SLAB_OBJECTS = (
    # X: a tube through all three layers; cut in two by the missing slab.
    ("X", (2, 22, 4, 8, 4, 8), {LA: 1, MA: 1, UA: 1}),
    # P, Q, R: across the X seam of a layer that exists in both runs.
    ("P", (2, 5, 10, 14, 28, 36), {LA: 2, LB: 1}),
    ("Q", (18, 21, 20, 24, 28, 36), {UA: 2, UB: 1}),
    ("R", (17, 20, 10, 14, 30, 34), {UA: 3, UB: 2}),
    # S: across the X seam inside the slab.
    ("S", (10, 13, 20, 24, 28, 36), {MA: 2, MB: 1}),
)


def pair(row) -> tuple:
    return (row["tile_a"], int(row["label_a"]), row["tile_b"], int(row["label_b"]))


class RerunMixin(StitchRunMixin):
    def candidates_run(self, tiles_dir, *extra_ids) -> Path:
        """Phases 1-3 in a new work directory."""
        work = self.make_dir() / "work"
        self.call("stitch_inventory", "--tiles", str(tiles_dir), "--work", str(work))
        self.call("stitch_ids", "--work", str(work), *extra_ids)
        self.call("stitch_candidates", "--work", str(work))
        return work

    def decide(self, work, by_pair: dict, *, name="decisions.csv", reviewer="vv") -> Path:
        """decisions.csv for ``{(tile_a, label_a, tile_b, label_b): decision}``."""
        candidates = read_rows(work / "candidates.csv")
        index = {pair(c): i for i, c in enumerate(candidates)}
        return write_decisions(work / name, candidates, {index[p]: d for p, d in by_pair.items()}, reviewer=reviewer)

    def carry(self, old, new, *decisions, extra=()):
        args = ["--old-work", str(old), "--work", str(new)]
        for path in decisions:
            args += ["--decisions", str(path)]
        return self.call("stitch_carry_decisions", *args, *extra)

    def report(self, work) -> dict:
        return {(r["old_tile_a"], int(r["label_a"]), r["old_tile_b"], int(r["label_b"])): r
                for r in read_rows(work / "carry_report.csv")}


class MissingSlabTests(RerunMixin, SimpleTestCase):
    """A tile set with a missing Z slab is stitched, then the slab arrives."""

    def setUp(self):
        self.old_tiles = self.make_dir()
        self.write_without_slab()
        self.old = self.candidates_run(self.old_tiles)
        self.human = self.decide(self.old, {(LA, 2, LB, 1): "yes", (UA, 2, UB, 1): "no"})
        self.auto = self.decide(self.old, {(UA, 3, UB, 2): "yes"}, name="decisions_auto.csv",
                                reviewer="auto:one-to-one,min_frac=0.5,min_contact=6")
        self.new_tiles = self.make_dir()
        write_tiles(self.new_tiles, (LA, LB, MA, MB, UA, UB), SLAB_OBJECTS)
        self.new = self.candidates_run(self.new_tiles)

    def write_without_slab(self):
        full = self.make_dir()
        write_tiles(full, (LA, LB, MA, MB, UA, UB), SLAB_OBJECTS)
        for name in (LA, LB, UA, UB):
            shutil.copy2(full / f"{name}.tif", self.old_tiles / f"{name}.tif")

    def test_old_decisions_do_not_match_the_new_run_directly(self):
        # What existed before: seam numbers and global IDs shift, so the earlier file is refused.
        old = {pair(c): (c["seam"], c["global_a"], c["global_b"]) for c in read_rows(self.old / "candidates.csv")}
        new = {pair(c): (c["seam"], c["global_a"], c["global_b"]) for c in read_rows(self.new / "candidates.csv")}
        self.assertNotEqual(old[(UA, 2, UB, 1)], new[(UA, 2, UB, 1)])
        with self.assertRaisesRegex(CommandError, "match no candidates.csv row"):
            self.call("stitch_objects", "--work", str(self.new), "--decisions", str(self.human))

    def test_valid_decisions_are_carried_and_only_new_seams_need_review(self):
        err = self.carry(self.old, self.new, self.human, self.auto)
        self.assertIn("tiles: 2 added, 0 removed", err)
        self.assertIn("earlier decisions: 3: 2 carried, 0 invalidated, 1 skipped (auto rule)", err)
        self.assertIn("4 need review", err)

        new = {pair(c): c for c in read_rows(self.new / "candidates.csv")}
        carried = read_rows(self.new / "decisions_carried.csv")
        self.assertEqual(list(carried[0]), list(stitching.DECISION_FIELDS))
        self.assertEqual({pair(r): (r["decision"], r["reviewer"], r["decided_at"]) for r in carried}, {
            (LA, 2, LB, 1): ("yes", "vv", "2026-11-04T10:00:00Z"),
            (UA, 2, UB, 1): ("no", "vv", "2026-11-04T10:00:00Z"),
        })
        for row in carried:     # re-keyed onto the new seam numbers and global IDs
            self.assertEqual((row["seam"], row["global_a"], row["global_b"]),
                             tuple(new[pair(row)][f] for f in ("seam", "global_a", "global_b")))

        report = self.report(self.new)
        self.assertEqual({k: (r["status"], r["reason"]) for k, r in report.items()}, {
            (LA, 2, LB, 1): ("carried", ""), (UA, 2, UB, 1): ("carried", ""),
            (UA, 3, UB, 2): ("skipped_auto", "auto_decision"),
        })
        needs = {pair(r): r["why"] for r in read_rows(self.new / "needs_review.csv")}
        self.assertEqual(needs, {
            (LA, 1, MA, 1): "new_seam", (MA, 1, UA, 1): "new_seam", (MA, 2, MB, 1): "new_seam",
            (UA, 3, UB, 2): "not_decided_before",       # auto-accepted earlier; the auto rule was not rerun
        })
        run = json.loads((self.new / "carry_run.json").read_text())
        self.assertEqual((run["counts"]["carried"], run["counts"]["needs_review"], run["old_work"]),
                         (2, 4, str(self.old.resolve())))

    def test_rerun_with_the_auto_rule_and_a_second_review_round(self):
        self.call("stitch_auto_decisions", "--work", str(self.new), "--min-contact", "6")
        err = self.carry(self.old, self.new, self.human, self.auto)
        needs = read_rows(self.new / "needs_review.csv")
        self.assertEqual(needs, [])                # every new pair here is clear-cut for the auto rule
        self.assertIn("0 need review", err)
        self.assertIn("1 carried decision(s) disagree with the new auto rule", err)     # Q: human said no
        self.assertIn("the new auto rule says yes", self.report(self.new)[(UA, 2, UB, 1)]["detail"])

        # Without the auto file the reviewer gets a page for exactly the pairs that need review.
        (self.new / "decisions_auto.csv").unlink()
        self.carry(self.old, self.new, self.human, self.auto)
        needs = read_rows(self.new / "needs_review.csv")
        images = self.make_dir()
        for name in (LA, LB, MA, MB, UA, UB):
            _, (z0, z1, y0, y1, x0, x1) = parse_tile_name(name)
            zz, yy, xx = np.meshgrid(np.arange(z0, z1), np.arange(y0, y1), np.arange(x0, x1), indexing="ij")
            _write_tiff(images / f"{name[:-2]}_raw.tif", em_value(zz, yy, xx).astype(np.uint8))
        self.call("stitch_review", "--work", str(self.new), "--images", str(images),
                  "--only", str(self.new / "needs_review.csv"))
        page = (self.new / "review_needs_review" / "index.html").read_text()
        self.assertEqual(page.count('"id": "c'), len(needs))

        # The reviewer answers them; carried + new decisions give the stitched objects.
        second = self.decide(self.new, {pair(r): "yes" for r in needs}, name="decisions_round2.csv")
        self.call("stitch_objects", "--work", str(self.new), "--decisions", str(self.new / "decisions_carried.csv"),
                  "--decisions", str(second), "--require-complete")
        self.call("stitch_measure", "--work", str(self.new), *VOXEL_ARGS)
        rows = self.measurements(self.new)
        tube = rows[self.object_of(self.new, LA, 1)]
        self.assertEqual((tube["member_count"], tube["tiles"], tube["touches_edge"]), ("3", f"{LA};{MA};{UA}", "false"))
        q = {self.object_of(self.new, UA, 2), self.object_of(self.new, UB, 1)}
        self.assertEqual(len(q), 2)                # the carried "no" kept Q apart

    def test_the_tube_was_cut_at_the_missing_slab_before(self):
        path = self.decide(self.old, {(LA, 2, LB, 1): "yes"})
        self.call("stitch_objects", "--work", str(self.old), "--decisions", str(path))
        self.call("stitch_measure", "--work", str(self.old), *VOXEL_ARGS)
        rows = self.measurements(self.old)
        lower, upper = rows[self.object_of(self.old, LA, 1)], rows[self.object_of(self.old, UA, 1)]
        self.assertEqual((lower["edge_faces"], upper["edge_faces"]), (f"{LA}:+z", f"{UA}:-z"))

    def test_refusals(self):
        with self.assertRaisesRegex(CommandError, "same directory"):
            self.carry(self.new, self.new, self.human)
        with self.assertRaisesRegex(CommandError, "match no candidates.csv row"):
            self.carry(self.new, self.old, self.decide(self.old, {(LA, 2, LB, 1): "yes"}, name="x.csv"),
                       self.decide(self.new, {(MA, 2, MB, 1): "yes"}, name="y.csv"))
        with self.assertRaisesRegex(CommandError, "needs region data"):
            self.carry(self.old, self.new, self.human, extra=("--region-only",))
        conflict = self.decide(self.old, {(LA, 2, LB, 1): "no"}, name="conflict.csv")
        with self.assertRaisesRegex(CommandError, "conflicting decisions"):
            self.carry(self.old, self.new, self.human, conflict)

    def test_delivery_readme_reports_carried_decisions(self):
        regions = self.make_dir()
        for name in (LA, LB, MA, MB, UA, UB):
            _write_tiff(regions / f"{name[:-2]}_mask.tif", np.ones((8, 32, 32), np.uint8))
        new = self.candidates_run(self.new_tiles, "--regions", str(regions))
        self.carry(self.old, new, self.human, extra=("--region-only",))
        needs = read_rows(new / "needs_review.csv")
        self.assertEqual(len(needs), 4)
        second = self.decide(new, {pair(r): "yes" for r in needs}, name="decisions_round2.csv")
        self.call("stitch_objects", "--work", str(new), "--decisions", str(new / "decisions_carried.csv"),
                  "--decisions", str(second))
        self.call("stitch_measure", "--work", str(new), *VOXEL_ARGS, "--region-only")
        self.call("stitch_deliver", "--work", str(new))
        readme = (new / "delivery" / "README.md").read_text()
        self.assertIn("**Carried over from an earlier run.** 2 of the reviewed decisions", readme)
        self.assertIn(f"(`{self.old.resolve()}`)", readme)
        self.assertIn("0 earlier decision(s) no longer applied", readme)
        self.assertIn("**Human review.** 6 pair(s)", readme)

    def test_include_auto_carries_rule_decisions_too(self):
        self.carry(self.old, self.new, self.human, self.auto, extra=("--include-auto",))
        carried = {pair(r): r["reviewer"] for r in read_rows(self.new / "decisions_carried.csv")}
        self.assertEqual(carried[(UA, 3, UB, 2)], "auto:one-to-one,min_frac=0.5,min_contact=6")
        self.assertEqual(len(carried), 3)


# Two tiles and a third; the right-hand tile is relabelled between the runs.
RA, RB, RC = "0-8_0-32_0-32_r", "0-8_0-32_32-64_r", "0-8_32-64_0-32_r"
RELABEL_OBJECTS = (
    ("K", (1, 4, 2, 6, 28, 36), {RA: 1, RB: 1}),       # unchanged
    ("L", (1, 4, 8, 12, 28, 44), {RA: 2, RB: 2}),      # edited away from the seam
    ("M", (1, 4, 14, 18, 28, 36), {RA: 3, RB: 3}),     # given a new ID
    ("N", (4, 8, 20, 24, 28, 36), {RA: 4, RB: 4}),     # its face on the seam shrinks
    ("O", (1, 4, 26, 30, 28, 36), {RA: 5, RB: 5}),     # pulled back from the seam
    ("T", (1, 4, 30, 34, 4, 8), {RA: 6, RC: 1}),       # across the Y seam to a tile that is removed
)


class RelabelledTileTests(RerunMixin, SimpleTestCase):
    def setUp(self):
        self.old_tiles = self.make_dir()
        write_tiles(self.old_tiles, (RA, RB, RC), RELABEL_OBJECTS)
        self.old = self.candidates_run(self.old_tiles)
        self.pairs = {name: (RA, ids[RA], *((RB, ids[RB]) if RB in ids else (RC, ids[RC])))
                      for name, _, ids in RELABEL_OBJECTS}
        self.human = self.decide(self.old, {self.pairs[n]: "yes" for n in "KLMNT"} | {self.pairs["O"]: "no"})

        self.new_tiles = self.make_dir()
        shutil.copy2(self.old_tiles / f"{RA}.tif", self.new_tiles / f"{RA}.tif")
        tile = tifffile.imread(self.old_tiles / f"{RB}.tif")
        tile[1:4, 8:12, 8:12] = 0           # L: trimmed far from the seam (x 40-44)
        tile[tile == 3] = 30                # M: same voxels, new ID
        tile[4:6, 20:24, 0:4] = 0           # N: half of its seam face removed
        tile[1:4, 26:30, 0] = 0             # O: one voxel back from the seam ...
        tile[1:4, 26:30, 4] = 5             # ... and one further in, so its voxel count is unchanged
        self.rb2 = RB[:-2] + "_proofread_v2"     # same box, another suffix
        _write_tiff(self.new_tiles / f"{self.rb2}.tif", tile)
        self.new = self.candidates_run(self.new_tiles)

    def test_each_change_is_invalidated_with_its_reason(self):
        err = self.carry(self.old, self.new, self.human)
        self.assertIn("tiles: 0 added, 1 removed", err)
        self.assertIn("earlier decisions: 6: 1 carried, 5 invalidated, 0 skipped", err)
        report = self.report(self.new)
        status = {name: (report[self.pairs[name]]["status"], report[self.pairs[name]]["reason"]) for name in "KLMNOT"}
        self.assertEqual(status, {
            "K": ("carried", ""),
            "L": ("invalidated", "label_changed"),
            "M": ("invalidated", "label_removed"),
            "N": ("invalidated", "label_changed;contact_changed"),
            "O": ("invalidated", "no_longer_touching"),
            "T": ("invalidated", "tile_removed"),
        })
        self.assertEqual(report[self.pairs["L"]]["detail"], "label_b 2 has 96 voxels, had 144")
        self.assertIn("contact_voxels 8, was 16", report[self.pairs["N"]]["detail"])
        self.assertIn(f"tile_b {RC} is not in the new run", report[self.pairs["T"]]["detail"])
        self.assertIn("label_changed: 2", err)
        self.assertIn(f"seam 1 {RA}:3 -> {RB}:3 (yes): label_b 3 is not in {self.rb2} any more", err)

        # K is carried onto the renamed tile.
        (carried,) = read_rows(self.new / "decisions_carried.csv")
        self.assertEqual(pair(carried), (RA, 1, self.rb2, 1))
        self.assertEqual(report[self.pairs["K"]]["new_tile_b"], self.rb2)

        # Only changed pairs need review; O no longer touches, T's tile is gone.
        needs = {pair(r): r["why"] for r in read_rows(self.new / "needs_review.csv")}
        self.assertEqual(needs, {
            (RA, 2, self.rb2, 2): "changed:label_changed",
            (RA, 3, self.rb2, 30): "new_candidate",
            (RA, 4, self.rb2, 4): "changed:label_changed;contact_changed",
        })

    def test_in_place_relabel_is_not_caught_without_the_carry_step(self):
        # The hazard this command removes: relabelled in place (same names, seams and IDs),
        # the earlier decisions still match candidates.csv and stitch_objects accepts them.
        before, in_place = self.make_dir(), self.make_dir()
        for directory, source in ((before, self.old_tiles / f"{RB}.tif"), (in_place, self.new_tiles / f"{self.rb2}.tif")):
            shutil.copy2(self.old_tiles / f"{RA}.tif", directory / f"{RA}.tif")
            shutil.copy2(source, directory / f"{RB}.tif")
        old, work = self.candidates_run(before), self.candidates_run(in_place)
        stale = self.decide(old, {self.pairs["L"]: "yes"}, name="stale.csv")
        self.call("stitch_objects", "--work", str(work), "--decisions", str(stale))        # accepted
        self.carry(old, work, stale)
        self.assertEqual(read_rows(work / "decisions_carried.csv"), [])
        self.assertEqual(self.report(work)[self.pairs["L"]]["reason"], "label_changed")

    def test_carried_decisions_are_accepted_by_stitch_objects(self):
        self.carry(self.old, self.new, self.human)
        self.call("stitch_objects", "--work", str(self.new), "--decisions", str(self.new / "decisions_carried.csv"))
        self.assertEqual(self.object_of(self.new, RA, 1), self.object_of(self.new, self.rb2, 1))
        self.assertNotEqual(self.object_of(self.new, RA, 2), self.object_of(self.new, self.rb2, 2))


# p0-like: tiles of two sizes. PA is 8 deep and 32 wide in y; PB is 12 deep and 48 wide; PC
# (added later) fills the space under PA.
PA, PB, PC = "0-8_0-32_0-32_p0", "0-12_0-48_32-64_p0", "8-12_0-32_0-32_p0"
MIXED_OBJECTS = (
    ("U", (2, 6, 10, 14, 20, 44), {PA: 1, PB: 1}),      # across the X seam, inside the shared face
    ("V", (9, 11, 4, 8, 26, 40), {PB: 2, PC: 1}),       # on PB's -x face below PA: an edge until PC arrives
    ("W", (3, 6, 36, 40, 32, 38), {PB: 3}),             # on PB's -x face beside PA (y 36-40): always an edge
    ("Y", (5, 11, 22, 26, 6, 10), {PA: 2, PC: 2}),      # across the Z seam PA -> PC
)


def clip_objects(objects, names):
    """``objects`` with only the local IDs of ``names`` (the others' tiles are absent)."""
    return [(name, box, {t: i for t, i in ids.items() if t in names}) for name, box, ids in objects]


class MixedTileSizeTests(RerunMixin, SimpleTestCase):
    def write(self, names):
        directory = self.make_dir()
        full = self.make_dir()
        write_tiles(full, (PA, PB, PC), MIXED_OBJECTS)
        for name in names:
            shutil.copy2(full / f"{name}.tif", directory / f"{name}.tif")
        return directory

    def test_seam_between_tiles_of_different_sizes(self):
        tiles_dir = self.write((PA, PB))
        work = self.candidates_run(tiles_dir)
        # The seam is the part of the two faces they share: PA's whole +x face, a part of PB's -x face.
        self.assertEqual(as_tuples(read_rows(work / "seams.csv"), stitching.SEAMS_FIELDS),
                         [(0, "x", "+x", PA, PB, 32, 0, 8, 0, 32, 32, 32, 256)])
        kinds = {(r["kind"], r["axis"]) for r in read_rows(work / "issues.csv")}
        self.assertEqual(kinds, {("irregular_axis", "z"), ("irregular_axis", "y")})     # reported, not blocking
        candidates = read_rows(work / "candidates.csv")
        self.assertEqual([pair(c) for c in candidates], [(PA, 1, PB, 1)])
        self.assertEqual((candidates[0]["contact_voxels"], candidates[0]["frac_a"]), ("16", "1.000000"))

        path = self.decide(work, {(PA, 1, PB, 1): "yes"})
        self.call("stitch_objects", "--work", str(work), "--decisions", str(path))
        self.call("stitch_measure", "--work", str(work), *VOXEL_ARGS)
        rows = self.assert_boxes_and_centres(work)
        untiled = self.untiled_measurements(tiles_dir, work)
        for object_id, row in rows.items():
            self.assertAlmostEqual(float(row["skeleton_length_um"]), untiled[object_id].skeleton_length_um, delta=1e-9)
        u = rows[self.object_of(work, PA, 1)]
        self.assertEqual((u["member_count"], u["touches_edge"]), ("2", "false"))
        # PB's -x face is only partly covered by PA: outside the shared face it is a volume edge.
        self.assertEqual(rows[self.object_of(work, PB, 2)]["edge_faces"], f"{PB}:-x")
        self.assertEqual(rows[self.object_of(work, PB, 3)]["edge_faces"], f"{PB}:-x")
        self.assertEqual(rows[self.object_of(work, PA, 2)]["edge_faces"], f"{PA}:+z")

    def test_review_card_on_a_partial_face(self):
        tiles_dir = self.write((PA, PB))
        work = self.candidates_run(tiles_dir)
        images = self.make_dir()
        for name in (PA, PB):
            _, (z0, z1, y0, y1, x0, x1) = parse_tile_name(name)
            zz, yy, xx = np.meshgrid(np.arange(z0, z1), np.arange(y0, y1), np.arange(x0, x1), indexing="ij")
            _write_tiff(images / f"{name[:-3]}_raw.tif", em_value(zz, yy, xx).astype(np.uint8))
        self.call("stitch_review", "--work", str(work), "--images", str(images), "--crop", "24")
        self.assertEqual((work / "review" / "index.html").read_text().count('"id": "c'), 1)
        self.assertTrue(list((work / "review" / "crops").glob("*.png")))

    def test_adding_a_tile_of_another_size_keeps_the_decision(self):
        old = self.candidates_run(self.write((PA, PB)))
        human = self.decide(old, {(PA, 1, PB, 1): "yes"})
        new_tiles = self.write((PA, PB, PC))
        new = self.candidates_run(new_tiles)
        self.assertEqual([(r["axis"], r["tile_a"], r["tile_b"], r["face_voxels"]) for r in read_rows(new / "seams.csv")],
                         [("z", PA, PC, "1024"), ("x", PA, PB, "256"), ("x", PC, PB, "128")])
        self.carry(old, new, human)
        self.assertEqual([pair(r) for r in read_rows(new / "decisions_carried.csv")], [(PA, 1, PB, 1)])
        needs = {pair(r): r["why"] for r in read_rows(new / "needs_review.csv")}
        self.assertEqual(needs, {(PA, 2, PC, 2): "new_seam", (PC, 1, PB, 2): "new_seam"})

        second = self.decide(new, dict.fromkeys(needs, "yes"), name="round2.csv")
        self.call("stitch_objects", "--work", str(new), "--decisions", str(new / "decisions_carried.csv"),
                  "--decisions", str(second), "--require-complete")
        self.call("stitch_measure", "--work", str(new), *VOXEL_ARGS)
        rows = self.assert_boxes_and_centres(new)
        untiled = self.untiled_measurements(new_tiles, new)
        for object_id, row in rows.items():
            self.assertAlmostEqual(float(row["skeleton_length_um"]), untiled[object_id].skeleton_length_um, delta=1e-9)
        v = rows[self.object_of(new, PB, 2)]
        self.assertEqual((v["member_count"], v["touches_edge"]), ("2", "false"))       # no longer an edge
        self.assertEqual(rows[self.object_of(new, PB, 3)]["edge_faces"], f"{PB}:-x")    # still beside PA, uncovered
        y = rows[self.object_of(new, PA, 2)]
        self.assertEqual((y["member_count"], y["touches_edge"]), ("2", "false"))


class CarryUnitTests(SimpleTestCase):
    def test_reasons_are_reported_in_a_fixed_order(self):
        self.assertEqual(stitching_rerun.REASONS, ("tile_removed", "label_removed", "label_changed",
                                                   "no_longer_touching", "contact_changed"))
        self.assertNotIn("decision", stitching_rerun.NEEDS_REVIEW_FIELDS)     # importing it decides nothing
