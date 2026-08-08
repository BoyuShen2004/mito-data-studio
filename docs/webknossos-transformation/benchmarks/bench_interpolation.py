#!/usr/bin/env python
"""Phase 8 — interpolation cost, memory and scaling.

Measures the pure core (no database) and the orchestration separately, because
they scale with different things: the core with pixels x depth, the service
with round trips.

Compares the bounded implementation against a **naive dense** alternative that
interpolates the whole plane when only a bounding box is dirty — the shape
ADR-006 §6 rules out — so the bounding-box requirement is justified by numbers
rather than assertion.

Runs entirely in-process; no database is required for the core section.

    python bench_interpolation.py
    python bench_interpolation.py --sizes 64,256,512 --depths 2,10,50
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

from annotation.interpolation import core  # noqa: E402


def discs(size: int, radius: int, shift: int):
    yy, xx = np.ogrid[:size, :size]
    c = size // 2
    a = np.zeros((size, size), np.uint8)
    b = np.zeros((size, size), np.uint8)
    a[((yy - c) ** 2 + (xx - (c - shift)) ** 2) <= radius ** 2] = 1
    b[((yy - c) ** 2 + (xx - (c + shift)) ** 2) <= radius ** 2] = 1
    return a, b


def many_labels(size: int, n_labels: int):
    """A plane carrying several segments; only one is interpolated."""
    a = np.zeros((size, size), np.uint16)
    b = np.zeros((size, size), np.uint16)
    yy, xx = np.ogrid[:size, :size]
    step = max(size // (n_labels + 1), 4)
    for i in range(1, n_labels + 1):
        cx = min(i * step, size - 1)
        r = max(step // 3, 3)
        a[((yy - size // 2) ** 2 + (xx - cx) ** 2) <= r ** 2] = i
        b[((yy - size // 2) ** 2 + (xx - cx) ** 2) <= (r + 1) ** 2] = i
    return a, b


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

    return {
        "p50_ms": round(statistics.median(times), 2),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "peak_kib": round(peak / 1024, 1),
        "result": result,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", default="64,256,512")
    ap.add_argument("--depths", default="2,10,50")
    args = ap.parse_args()

    sizes = [int(s) for s in args.sizes.split(",")]
    depths = [int(d) for d in args.depths.split(",")]
    out = {"by_size": [], "by_depth": [], "by_labels": [],
           "sparsity": [], "bounded_vs_dense": [], "anisotropy": []}

    # --- scaling with plane size -------------------------------------------
    for size in sizes:
        a, b = discs(size, radius=size // 6, shift=size // 12)
        m = measure(lambda: core.plan(a, b, label_id=1, depth=4))
        plan = m.pop("result")
        out["by_size"].append({
            "size": size, "pixels": size * size, "depth": 4,
            "voxels_changed": plan.voxels_changed, **m,
        })

    # --- scaling with depth ------------------------------------------------
    a, b = discs(256, radius=42, shift=20)
    for depth in depths:
        m = measure(lambda d=depth: core.plan(a, b, label_id=1, depth=d))
        plan = m.pop("result")
        out["by_depth"].append({
            "size": 256, "depth": depth,
            "voxels_changed": plan.voxels_changed, **m,
        })

    # --- many labels present, one interpolated -----------------------------
    for n in (1, 8, 64):
        a, b = many_labels(256, n)
        m = measure(lambda: core.plan(a, b, label_id=1, depth=4))
        m.pop("result")
        out["by_labels"].append({"labels_present": n, "size": 256, **m})

    # --- sparse vs dense mask ----------------------------------------------
    for name, radius in (("sparse", 8), ("dense", 110)):
        a, b = discs(256, radius=radius, shift=4)
        m = measure(lambda: core.plan(a, b, label_id=1, depth=4))
        plan = m.pop("result")
        out["sparsity"].append({
            "mask": name, "radius": radius,
            "fill_fraction": round(float((a > 0).mean()), 4),
            "voxels_changed": plan.voxels_changed, **m,
        })

    # --- bounded box vs naive dense ----------------------------------------
    # The object occupies a small box inside a large plane. The bounded call
    # interpolates only the box; the naive one interpolates the whole plane.
    full = 1024
    a_full, b_full = discs(full, radius=30, shift=10)
    ys = np.where(a_full.any(axis=1) | b_full.any(axis=1))[0]
    xs = np.where(a_full.any(axis=0) | b_full.any(axis=0))[0]
    pad = 4
    y0, y1 = max(ys[0] - pad, 0), min(ys[-1] + pad + 1, full)
    x0, x1 = max(xs[0] - pad, 0), min(xs[-1] + pad + 1, full)
    a_box, b_box = a_full[y0:y1, x0:x1], b_full[y0:y1, x0:x1]

    dense = measure(lambda: core.plan(a_full, b_full, label_id=1, depth=8),
                    repeats=3)
    dense_plan = dense.pop("result")
    bounded = measure(lambda: core.plan(a_box, b_box, label_id=1, depth=8),
                      repeats=3)
    bounded_plan = bounded.pop("result")
    out["bounded_vs_dense"] = [
        {"strategy": "naive dense (full plane)", "plane": [full, full],
         "voxels_changed": dense_plan.voxels_changed, **dense},
        {"strategy": "bounded (bbox only)",
         "plane": [int(y1 - y0), int(x1 - x0)],
         "voxels_changed": bounded_plan.voxels_changed, **bounded},
    ]

    # --- anisotropy cost ---------------------------------------------------
    a, b = discs(256, radius=42, shift=20)
    for spacing in ((1.0, 1.0), (4.0, 1.0)):
        m = measure(lambda s=spacing: core.plan(a, b, label_id=1, depth=4,
                                                spacing=s))
        m.pop("result")
        out["anisotropy"].append({"spacing": list(spacing), **m})

    print(json.dumps(out, indent=2))

    # Guard: the bounded strategy must beat the dense one it exists to replace,
    # and must agree on what it labels inside the box.
    d, bd = out["bounded_vs_dense"]
    if bd["p50_ms"] >= d["p50_ms"]:
        print("FAIL: bounded interpolation was not faster than dense",
              file=sys.stderr)
        return 1
    if bd["voxels_changed"] != d["voxels_changed"]:
        print(f"FAIL: bounded and dense disagree "
              f"({bd['voxels_changed']} vs {d['voxels_changed']} voxels)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
