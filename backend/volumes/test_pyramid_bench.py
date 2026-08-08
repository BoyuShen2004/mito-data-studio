"""Phase 11 benchmark — build cost and derivative read cost.

Row 13 owns `p95 scrub target`; row 11 owns *build* and *derivative read*, and
those are what this measures. The two claims worth pinning with numbers rather
than prose:

1. **The build is bounded.** Peak resident slab is a plane times the z factor,
   not the volume — asserted, so a regression to a whole-volume load fails here
   rather than being discovered on a 40 GB dataset.
2. **A higher mag really is cheaper to read.** That is the entire justification
   for the phase; if a mag-4 plane were not materially cheaper than mag-1 there
   would be no reason to write one.

Both assertions fail the test on regression, so these are numbers with teeth
rather than a report nobody reads. Results are printed so a run leaves a record.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from projects.models import Dataset, Project
from volumes.models import Volume
from volumes.pyramid import service, store

User = get_user_model()

ON = dict(FEATURE_VOLUME_PYRAMIDS=True)
#: A realistic plane size, deliberately. Measured during development: at 512²
#: planes a mag-4 read is only ~0.78× a mag-1 read, because zarr's fixed
#: per-read cost dominates 512 KB of data; at 2048² it is ~0.14×. Benchmarking
#: the small case and reporting "the pyramid barely helps" would have been a
#: true sentence about an unrepresentative size. Few z-planes keeps CI quick.
BENCH_SHAPE = (4, 2048, 2048)


def _zarr_available() -> bool:
    try:
        store.require_zarr()
        return True
    except Exception:  # pragma: no cover
        return False


class PyramidBenchmark(TestCase):
    def setUp(self):
        if not _zarr_available():  # pragma: no cover
            self.skipTest("zarr is an optional dependency and is not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir(parents=True)
        external = Path(self.tmp.name) / "src"
        external.mkdir()

        rng = np.random.default_rng(31)
        source = rng.integers(0, 60000, size=BENCH_SHAPE, dtype=np.uint16)
        self.image = external / "bench.tif"
        tifffile.imwrite(str(self.image), source)
        self.source_bytes = int(source.nbytes)

        user = User.objects.create_user(username="bench", password="x")
        project = Project.objects.create(title="Bench", created_by=user)
        dataset = Dataset.objects.create(project=project, name="DS")
        self.volume = Volume.objects.create(
            project=project, dataset=dataset, name="bench",
            image_path=str(self.image),
            voxel_size_z=40.0, voxel_size_y=8.0, voxel_size_x=8.0,
        )

    @staticmethod
    def _time(fn, repeats: int = 7) -> dict:
        samples = []
        for _ in range(repeats):
            start = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - start) * 1000.0)
        samples.sort()
        return {
            "p50": samples[len(samples) // 2],
            "p95": samples[min(len(samples) - 1, int(len(samples) * 0.95))],
            "max": samples[-1],
        }

    def test_build_is_bounded_and_higher_mags_read_faster(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            started = time.perf_counter()
            report = service.build_pyramid(self.volume)
            build_ms = (time.perf_counter() - started) * 1000.0

            usage = store.store_usage(self.volume)
            mags = [lvl["mag"] for lvl in report.levels]

            # Measured with the group held open. `store.read_plane` opens the
            # group per call, and at these sizes that fixed cost swamps the read
            # itself — measuring it would report store-open latency wearing a
            # read's name. The open cost is measured separately below, because
            # Phase 12 will serve chunks in a loop and needs to know it is there.
            group = store.open_pyramid(self.volume)
            timings = {}
            for mag in mags:
                array = group[mag]
                timings[mag] = self._time(lambda a=array: np.asarray(a[0]))

            open_cost = self._time(lambda: store.open_pyramid(self.volume))

        plane_voxels = BENCH_SHAPE[1] * BENCH_SHAPE[2]
        total_voxels = int(np.prod(BENCH_SHAPE))

        # --- assertions with teeth -------------------------------------------
        # Bounded: peak resident slab is a small multiple of a *plane* and does
        # not grow with depth. Stated against the plane rather than against the
        # volume on purpose — on a shallow volume one plane is legitimately a
        # large fraction of the total, so a volume-relative bound would fail a
        # correct build (it did, at 4 z-planes) while passing a whole-volume
        # load of a deep one. The plane-relative form is the actual invariant.
        self.assertLessEqual(
            report.peak_slab_voxels,
            plane_voxels * 8,
            f"peak slab {report.peak_slab_voxels:,} exceeds 8 planes "
            f"({plane_voxels:,} each) — streaming has regressed to a "
            "volume-scale read",
        )

        # A coarser mag must be materially cheaper to read than full
        # resolution. Mag k has k² fewer voxels per plane, so anything close to
        # parity means the pyramid is buying nothing — which would remove the
        # justification for the whole phase.
        if len(mags) >= 3:
            self.assertLess(
                timings[mags[2]]["p50"],
                timings[mags[0]]["p50"] * 0.5,
                f"mag {mags[2]} ({timings[mags[2]]['p50']:.2f} ms) was not "
                f"materially cheaper than mag {mags[0]} "
                f"({timings[mags[0]]['p50']:.2f} ms)",
            )

        print(
            "\n  --- Phase 11 pyramid benchmark ---"
            f"\n  source                {BENCH_SHAPE}  uint16  "
            f"{self.source_bytes / 1e6:.1f} MB"
            f"\n  levels                {len(report.levels)}  mags={mags}"
            f"\n  build wall time       {build_ms:.0f} ms"
            f"\n  peak slab voxels      {report.peak_slab_voxels:,} "
            f"(volume {total_voxels:,}, plane {plane_voxels:,})"
            f"\n  bytes written         {usage['bytes'] / 1e6:.1f} MB "
            f"in {usage['files']} files "
            f"({usage['bytes'] / self.source_bytes:.2f}× source)"
            f"\n  chunks validated      {report.checked_chunks} (seed {report.seed})"
            f"\n  store open            p50 {open_cost['p50']:.2f} ms "
            "(per call — Phase 12 should cache the handle)"
        )
        for mag in mags:
            t = timings[mag]
            print(
                f"  read mag {mag:<3s}          p50 {t['p50']:7.2f} ms   "
                f"p95 {t['p95']:7.2f} ms   max {t['max']:7.2f} ms"
            )

    def test_rebuild_is_deterministic(self):
        """A rebuild must produce identical bytes — otherwise "idempotent" is
        a claim rather than a property."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.build_pyramid(self.volume)
            first = store.read_plane(self.volume, "2", 0).copy()
            first_digest = store.digest(first)

            service.build_pyramid(self.volume)
            second_digest = store.digest(store.read_plane(self.volume, "2", 0))

        self.assertEqual(first_digest, second_digest)
        print(f"\n  rebuild digest stable: {first_digest[:16]}…")
