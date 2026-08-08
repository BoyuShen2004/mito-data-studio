#!/usr/bin/env python
"""Generate Phase 9 flood-fill golden fixtures.

**Must never import the flood-fill core.** Expected outputs come from
construction: each case is built so the correct answer is known by geometry
before any code runs — a filled rectangle bounded by a wall fills exactly the
rectangle's interior, a region split by a complete wall fills only the seed's
side, and a single-voxel gap in a wall is a connection under 4-connectivity but
a diagonal gap is not.

Small enough to verify by hand, which is the point: a golden fixture nobody can
check is not evidence.

    python make_tool_fixtures.py            # write
    python make_tool_fixtures.py --check    # verify hashes
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TOOL = "flood_fill"
TOOL_SCHEMA_VERSION = 1


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def build_cases():
    cases = []

    # 1. open rectangle: seed anywhere fills the whole background rectangle.
    block = np.zeros((1, 8, 8), np.uint8)
    expected = np.ones((1, 8, 8), bool)
    cases.append(dict(
        id="open_plane", block=block, seed=(0, 4, 4), expected=expected,
        rationale="An empty 8x8 plane is one connected background region, so a "
                  "seed anywhere reaches every voxel.",
    ))

    # 2. walled box: a closed border confines the fill to the interior.
    block = np.zeros((1, 8, 8), np.uint8)
    block[0, 0, :] = 9; block[0, -1, :] = 9
    block[0, :, 0] = 9; block[0, :, -1] = 9
    expected = np.zeros((1, 8, 8), bool)
    expected[0, 1:-1, 1:-1] = True
    cases.append(dict(
        id="walled_box", block=block, seed=(0, 4, 4), expected=expected,
        rationale="A complete border of label 9 confines a background fill to "
                  "the 6x6 interior. Counted by hand.",
    ))

    # 3. complete vertical wall: only the seed's side fills.
    block = np.zeros((1, 8, 8), np.uint8)
    block[0, :, 4] = 9
    expected = np.zeros((1, 8, 8), bool)
    expected[0, :, 0:4] = True
    cases.append(dict(
        id="split_by_wall", block=block, seed=(0, 4, 1), expected=expected,
        rationale="A full-height wall at column 4 disconnects the plane; a "
                  "seed on the left fills columns 0-3 only.",
    ))

    # 4. one-voxel gap: 4-connectivity passes straight through it.
    block = np.zeros((1, 8, 8), np.uint8)
    block[0, :, 4] = 9
    block[0, 3, 4] = 0                      # a single hole in the wall
    expected = np.ones((1, 8, 8), bool)
    expected[0, :, 4] = False
    expected[0, 3, 4] = True                # the gap itself is background
    cases.append(dict(
        id="gap_in_wall", block=block, seed=(0, 4, 1), expected=expected,
        rationale="One background voxel in the wall connects both sides under "
                  "4-connectivity, so the fill escapes. Everything except the "
                  "remaining wall is reached.",
    ))

    # 5. diagonal gap: 4-connectivity does NOT leak; 8-connectivity would.
    #    This is the case that pins the connectivity choice.
    block = np.zeros((1, 7, 7), np.uint8)
    block[0, :, 3] = 9
    block[0, 3, 3] = 9
    block[0, 2, 2] = 9; block[0, 2, 4] = 9   # close the orthogonal routes
    block[0, 4, 2] = 9; block[0, 4, 4] = 9
    block[0, 3, 2] = 0; block[0, 3, 4] = 0
    # With the wall solid at column 3, the right half is unreachable.
    expected = np.zeros((1, 7, 7), bool)
    for r in range(7):
        for c in range(3):
            if block[0, r, c] == 0:
                expected[0, r, c] = True
    cases.append(dict(
        id="diagonal_no_leak", block=block, seed=(0, 3, 0), expected=expected,
        rationale="The wall is solid at column 3, so under 4-connectivity the "
                  "fill cannot cross. 8-connectivity would leak diagonally, "
                  "which is exactly why 4 was chosen.",
    ))

    # 6. 3-D: two stacked slices connected through z.
    block = np.zeros((3, 4, 4), np.uint8)
    block[1, :, :] = 9                      # middle slice fully blocked
    expected = np.zeros((3, 4, 4), bool)
    expected[0, :, :] = True                # only the seed's slice
    cases.append(dict(
        id="z_blocked", block=block, seed=(0, 2, 2), expected=expected,
        rationale="A fully-labelled middle slice disconnects z, so a 3-D fill "
                  "seeded on slice 0 stays on slice 0.",
    ))

    block = np.zeros((3, 4, 4), np.uint8)
    expected = np.ones((3, 4, 4), bool)
    cases.append(dict(
        id="z_connected", block=block, seed=(0, 2, 2), expected=expected,
        rationale="An empty 3x4x4 block is one connected region under "
                  "6-connectivity, so the fill spans all three slices.",
    ))

    # 7. seed on a labelled voxel fills that label's component, not background.
    block = np.zeros((1, 6, 6), np.uint8)
    block[0, 1:4, 1:4] = 5
    expected = np.zeros((1, 6, 6), bool)
    expected[0, 1:4, 1:4] = True
    cases.append(dict(
        id="seed_on_label", block=block, seed=(0, 2, 2), expected=expected,
        rationale="Seeding inside a 3x3 block of label 5 fills exactly that "
                  "block: the fill follows the seed's own label.",
    ))

    # 8. single voxel isolated by labels.
    block = np.zeros((1, 5, 5), np.uint8)
    block[0, :, :] = 9
    block[0, 2, 2] = 0
    expected = np.zeros((1, 5, 5), bool)
    expected[0, 2, 2] = True
    cases.append(dict(
        id="isolated_voxel", block=block, seed=(0, 2, 2), expected=expected,
        rationale="A lone background voxel surrounded by label 9 fills only "
                  "itself.",
    ))
    return cases


def write(check_only=False) -> int:
    cases = build_cases()
    manifest = {
        "generated_by": "make_tool_fixtures.py",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": TOOL,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "provenance": (
            "Expected outputs constructed by hand from geometry. This "
            "generator does not import the flood-fill core. Tool ranked P1 in "
            "docs/webknossos-transformation/19-target-annotation-design.md."
        ),
        "axis_order": "(z, y, x)",
        "dtype": "uint8 labels, bool expected masks",
        "connectivity": "4 in-plane, 6 in 3-D",
        "tolerance": "exact equality — flood fill is discrete and has no "
                     "floating-point component",
        "cases": [],
    }
    bad = 0
    for case in cases:
        path = HERE / f"{TOOL}_{case['id']}.npz"
        arrays = {"block": case["block"], "expected": case["expected"]}
        if check_only:
            if not path.exists():
                print(f"MISSING {path.name}"); bad += 1; continue
            existing = np.load(path)
            for k, v in arrays.items():
                if k not in existing or sha(existing[k]) != sha(v):
                    print(f"CHANGED {path.name}:{k}"); bad += 1
        else:
            np.savez_compressed(path, **arrays)
        manifest["cases"].append({
            "id": case["id"], "tool": TOOL, "fixture": path.name,
            "seed": list(case["seed"]),
            "block_shape": list(case["block"].shape),
            "expected_voxels": int(case["expected"].sum()),
            "hashes": {k: sha(v) for k, v in arrays.items()},
            "rationale": case["rationale"],
        })
    if not check_only:
        (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"wrote {len(cases)} fixtures + manifest.json")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    bad = write(check_only=args.check)
    if args.check:
        print("fixtures match generator" if not bad else f"{bad} mismatch(es)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
