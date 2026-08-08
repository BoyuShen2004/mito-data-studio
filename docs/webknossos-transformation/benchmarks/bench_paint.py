"""Phase 0 baseline: paint/save latency (the label write path).

Measures `services.set_label_slice_ids` — what every brush-stroke commit and
every explicit Save runs — plus the `get_label_slice_ids` read that precedes
it. Uses a scratch MITO_DATA_ROOT with the real image symlinked in, so the
source volume is never written to and nothing lands in the repo.

Usage:
    python bench_paint.py <abs-path-to-image.tif> [--n 40]
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

_TMPDIR = tempfile.mkdtemp(prefix="mito-paint-")
os.environ["MITO_DATA_ROOT"] = _TMPDIR

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

settings.DATABASES["default"]["NAME"] = str(Path(_TMPDIR) / "paint.sqlite3")

import numpy as np  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.core.management import call_command  # noqa: E402

from annotation import services  # noqa: E402
from annotation.visualization import slice_io  # noqa: E402
from projects.models import Dataset, Project  # noqa: E402
from volumes.models import Volume  # noqa: E402


def rss_mb() -> float:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return float("nan")


def summarize(name: str, ms: list[float]) -> dict:
    return {
        "op": name,
        "n": len(ms),
        "p50_ms": round(statistics.median(ms), 1),
        "p95_ms": round(float(np.percentile(ms, 95)), 1),
        "max_ms": round(max(ms), 1),
    }


def brush_runs(shape, label_id: int, frac: float = 0.02):
    """A slice whose painted area is `frac` of the plane — a plausible stroke."""
    h, w = shape
    sl = np.zeros((h, w), dtype=np.int32)
    side = max(1, int((h * w * frac) ** 0.5))
    sl[h // 2 : h // 2 + side, w // 2 : w // 2 + side] = label_id
    return slice_io.encode_label_rle(sl), sl


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--n", type=int, default=40)
    args = ap.parse_args()

    src = Path(args.image).resolve()
    link = Path(_TMPDIR) / "bench" / "image.tif"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(src)  # read-only use of the real volume

    call_command("migrate", run_syncdb=True, verbosity=0)
    User = get_user_model()
    owner = User.objects.create_user("paint-owner", password="x")
    proj = Project.objects.create(title="Paint bench", created_by=owner)
    ds = Dataset.objects.create(project=proj, name="ds")
    vol = Volume.objects.create(
        project=proj, dataset=ds, name="v", image_path="bench/image.tif"
    )

    meta = slice_io.volume_meta("bench/image.tif")
    shape = meta["shape"]
    zmid = shape["z"] // 2
    plane = (shape["y"], shape["x"])
    results = {
        "image": str(src),
        "shape": shape,
        "mpix_per_z_slice": round(shape["y"] * shape["x"] / 1e6, 1),
        "label_dtype": "uint16",
        "working_label_size_gb": round(
            shape["z"] * shape["y"] * shape["x"] * 2 / 1e9, 2
        ),
    }
    rss0 = rss_mb()

    runs, _ = brush_runs(plane, 1)

    # --- first paint in a fresh worker: creates the working label memmap
    #     (whole-volume file) and pays the one-time O(volume) mm.max() scan.
    gc.collect()
    t0 = time.perf_counter()
    services.set_label_slice_ids(vol, "z", zmid, list(plane), runs, origin="manual")
    results["cold_first_paint_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    results["rss_after_first_paint_mb"] = round(rss_mb(), 1)

    # --- warm paint: the steady-state per-stroke / per-Save cost -----------
    warm = []
    for i in range(args.n):
        r, _ = brush_runs(plane, (i % 5) + 1)
        t0 = time.perf_counter()
        services.set_label_slice_ids(
            vol, "z", zmid + (i % 8), list(plane), r, origin="manual"
        )
        warm.append((time.perf_counter() - t0) * 1000)
    results["warm_paint_z"] = summarize("set_label_slice_ids_z", warm)

    # --- the read the editor issues when you land on a slice ---------------
    reads = []
    for i in range(min(args.n, 20)):
        t0 = time.perf_counter()
        services.get_label_slice_ids(vol, "z", zmid + (i % 8))
        reads.append((time.perf_counter() - t0) * 1000)
    results["label_read_z"] = summarize("get_label_slice_ids_z", reads)

    # --- a y-axis write spans every z, so it drops the 3D summary cache ----
    yruns, _ = brush_runs((shape["z"], shape["x"]), 7)
    ywrites = []
    for i in range(5):
        t0 = time.perf_counter()
        services.set_label_slice_ids(
            vol, "y", shape["y"] // 2 + i, [shape["z"], shape["x"]], yruns,
            origin="manual",
        )
        ywrites.append((time.perf_counter() - t0) * 1000)
    results["warm_paint_y"] = summarize("set_label_slice_ids_y", ywrites)

    results["rss_end_mb"] = round(rss_mb(), 1)
    results["rss_growth_mb"] = round(rss_mb() - rss0, 1)

    print(json.dumps(results, indent=2))

    shutil.rmtree(_TMPDIR, ignore_errors=True)


if __name__ == "__main__":
    main()
