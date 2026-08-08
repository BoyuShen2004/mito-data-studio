"""Phase 0 baseline: server-side slice-IO hot path for the in-app viewer.

Read-only. Measures what Django does per viewer request today, so later phases
have numbers to beat. Points at a volume via MITO_DATA_ROOT + a relative
location, exactly like ``VolumeSliceView`` does.

Usage:
    python bench_slice_io.py <location-relative-to-MITO_DATA_ROOT> [--axis z]
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

import numpy as np  # noqa: E402
from annotation.visualization import slice_io  # noqa: E402


def rss_mb() -> float:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return float("nan")


def pct(values: list[float], p: float) -> float:
    return float(np.percentile(values, p))


def summarize(name: str, samples_ms: list[float]) -> dict:
    return {
        "op": name,
        "n": len(samples_ms),
        "p50_ms": round(statistics.median(samples_ms), 1),
        "p95_ms": round(pct(samples_ms, 95), 1),
        "max_ms": round(max(samples_ms), 1),
        "mean_ms": round(statistics.mean(samples_ms), 1),
    }


def timed(fn, *a, **kw) -> float:
    t0 = time.perf_counter()
    fn(*a, **kw)
    return (time.perf_counter() - t0) * 1000.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("location")
    ap.add_argument("--axis", default="z")
    ap.add_argument("--n", type=int, default=60, help="slices per scrub run")
    args = ap.parse_args()

    results: dict = {"location": args.location, "axis": args.axis}
    rss_start = rss_mb()

    # --- cold: what a first viewer load pays ------------------------------
    slice_io.clear_caches()
    gc.collect()
    t_meta = timed(slice_io.volume_meta, args.location)
    meta = slice_io.volume_meta(args.location)
    results["meta"] = meta
    results["cold_volume_meta_ms"] = round(t_meta, 1)

    shape = meta["shape"]
    n_axis = shape[args.axis]
    mid = n_axis // 2

    slice_io.clear_caches()
    gc.collect()
    t_first = timed(slice_io.render_image_slice_jpeg, args.location, args.axis, mid)
    results["cold_first_slice_jpeg_ms"] = round(t_first, 1)
    results["rss_after_cold_mb"] = round(rss_mb(), 1)

    # --- warm sequential scrub (slider dragged one step at a time) --------
    seq = [
        timed(slice_io.render_image_slice_jpeg, args.location, args.axis, i)
        for i in range(mid, min(mid + args.n, n_axis))
    ]
    results["sequential_scrub"] = summarize("sequential_scrub_jpeg", seq)

    # --- random scrub (slider thrash / jump-to-slice) ---------------------
    rnd = random.Random(1234)
    picks = [rnd.randrange(n_axis) for _ in range(args.n)]
    rand = [
        timed(slice_io.render_image_slice_jpeg, args.location, args.axis, i)
        for i in picks
    ]
    results["random_scrub"] = summarize("random_scrub_jpeg", rand)

    # --- cache-defeating scrub: what happens past the LRU -----------------
    # MAX_SLICE_CACHE=256 / MAX_ENCODED_CACHE=512, so a long session or a
    # second volume evicts everything. Measure the uncached steady state.
    nocache = []
    for i in picks[:20]:
        slice_io._slice_cache.clear()
        slice_io._encoded_cache.clear()
        nocache.append(
            timed(slice_io.render_image_slice_jpeg, args.location, args.axis, i)
        )
    results["uncached_scrub"] = summarize("uncached_scrub_jpeg", nocache)

    # --- orthogonal axes: memmap is z-major, so y/x cut across the file ---
    for ax in ("y", "x"):
        if ax == args.axis:
            continue
        slice_io.clear_caches()
        gc.collect()
        n_ax = shape[ax]
        ortho = [
            timed(slice_io.render_image_slice_jpeg, args.location, ax, n_ax // 2 + k)
            for k in range(5)
        ]
        results[f"orthogonal_{ax}"] = summarize(f"orthogonal_{ax}_jpeg", ortho)

    results["rss_after_scrub_mb"] = round(rss_mb(), 1)

    # --- concurrency: several viewer tabs / gunicorn threads --------------
    for workers in (4, 12):
        slice_io.clear_caches()
        gc.collect()
        idxs = [rnd.randrange(n_axis) for _ in range(workers * 4)]
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            lat = list(
                ex.map(
                    lambda i: timed(
                        slice_io.render_image_slice_jpeg, args.location, args.axis, i
                    ),
                    idxs,
                )
            )
        wall = (time.perf_counter() - t0) * 1000.0
        s = summarize(f"concurrent_{workers}_jpeg", lat)
        s["wall_ms"] = round(wall, 1)
        s["throughput_slices_per_s"] = round(len(idxs) / (wall / 1000.0), 1)
        results[f"concurrent_{workers}"] = s

    results["rss_end_mb"] = round(rss_mb(), 1)
    results["rss_growth_mb"] = round(rss_mb() - rss_start, 1)
    results["cache_stats"] = slice_io.cache_stats()

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
