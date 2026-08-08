#!/usr/bin/env python
"""Phase 9 — flood fill cost, memory and scaling.

The claim ADR-007 §7 makes is that a tool request is bounded: confined to the
caller's box, capped, and unable to trigger a full-volume scan. This measures
that, and compares bounded processing against the full-plane alternative.

    python bench_tools.py
    python bench_tools.py --sizes 64,256,512
"""

from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND))

from annotation.tools import flood_fill  # noqa: E402
from annotation.tools.common import BoundingBox  # noqa: E402


def measure(fn, repeats=5):
    times = []
    gc.collect()
    tracemalloc.start()
    for _ in range(repeats):
        t0 = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - t0) * 1000)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    times.sort()

    def pct(p):
        return round(times[min(int(len(times) * p), len(times) - 1)], 2)

    return {"p50_ms": round(statistics.median(times), 2),
            "p95_ms": pct(0.95), "p99_ms": pct(0.99),
            "peak_kib": round(peak / 1024, 1), "result": result}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", default="64,256,512")
    args = ap.parse_args()
    sizes = [int(s) for s in args.sizes.split(",")]
    out = {"by_size": [], "sparsity": [], "components": [],
           "depth_3d": [], "bounded_vs_full": []}

    # --- scaling with plane size (open region: worst case, fills everything) --
    for size in sizes:
        block = np.zeros((1, size, size), np.uint8)
        bbox = BoundingBox(0, 0, 0, 1, size, size)
        m = measure(lambda: flood_fill.plan(block, seed=(0, 0, 0), label_id=1,
                                            bbox=bbox,
                                            max_voxels=64 * 1024 * 1024))
        plan = m.pop("result")
        out["by_size"].append({"size": size, "pixels": size * size,
                               "voxels_changed": plan.voxels_changed, **m})

    # --- sparse (small component) vs dense (whole plane) --------------------
    size = 256
    for name, walled in (("dense_open", False), ("sparse_walled", True)):
        block = np.zeros((1, size, size), np.uint8)
        if walled:
            block[0, 10, :] = 9
            block[0, :, 10] = 9
        bbox = BoundingBox(0, 0, 0, 1, size, size)
        m = measure(lambda: flood_fill.plan(block, seed=(0, 0, 0), label_id=1,
                                            bbox=bbox))
        plan = m.pop("result")
        out["sparsity"].append({"case": name,
                                "voxels_changed": plan.voxels_changed, **m})

    # --- many tiny components (pathological) --------------------------------
    block = np.zeros((1, 256, 256), np.uint8)
    block[0, 1::2, :] = 9
    block[0, :, 1::2] = 9
    bbox = BoundingBox(0, 0, 0, 1, 256, 256)
    m = measure(lambda: flood_fill.plan(block, seed=(0, 0, 0), label_id=1,
                                        bbox=bbox))
    plan = m.pop("result")
    out["components"].append({"case": "16k_isolated_voxels",
                              "voxels_changed": plan.voxels_changed, **m})

    # --- 3-D depth ----------------------------------------------------------
    for depth in (1, 8, 32):
        block = np.zeros((depth, 128, 128), np.uint8)
        bbox = BoundingBox(0, 0, 0, depth, 128, 128)
        m = measure(lambda: flood_fill.plan(block, seed=(0, 0, 0), label_id=1,
                                            bbox=bbox, max_depth=32))
        plan = m.pop("result")
        out["depth_3d"].append({"depth": depth,
                                "voxels_changed": plan.voxels_changed, **m})

    # --- bounded box vs full plane -----------------------------------------
    # The object is a small walled room inside a large plane. Bounded processes
    # the room; full processes the whole plane to reach the same answer.
    full = 2048
    plane = np.zeros((1, full, full), np.uint8)
    plane[0, 100:160, 100] = 9
    plane[0, 100:160, 160] = 9
    plane[0, 100, 100:161] = 9
    plane[0, 160, 100:161] = 9
    room = plane[:, 100:161, 100:161].copy()

    fullm = measure(lambda: flood_fill.plan(
        plane, seed=(0, 130, 130), label_id=1,
        bbox=BoundingBox(0, 0, 0, 1, full, full),
        max_voxels=64 * 1024 * 1024), repeats=3)
    full_plan = fullm.pop("result")
    boundm = measure(lambda: flood_fill.plan(
        room, seed=(0, 30, 30), label_id=1,
        bbox=BoundingBox(0, 0, 0, 1, room.shape[1], room.shape[2])), repeats=3)
    bound_plan = boundm.pop("result")
    out["bounded_vs_full"] = [
        {"strategy": "full plane", "plane": [full, full],
         "voxels_changed": full_plan.voxels_changed, **fullm},
        {"strategy": "bounded bbox", "plane": list(room.shape[1:]),
         "voxels_changed": bound_plan.voxels_changed, **boundm},
    ]

    print(json.dumps(out, indent=2))
    f, b = out["bounded_vs_full"]
    if b["p50_ms"] >= f["p50_ms"]:
        print("FAIL: bounded was not faster than full-plane", file=sys.stderr)
        return 1
    if b["voxels_changed"] != f["voxels_changed"]:
        print(f"FAIL: bounded and full disagree ({b['voxels_changed']} vs "
              f"{f['voxels_changed']})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
