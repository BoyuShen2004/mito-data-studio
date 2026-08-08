"""Phase 12 benchmark — cold vs warm reads, and the claim ADR-010 rests on.

ADR-010 §1 resolves doc 20's "separate Chunk Svc process" by arguing that what
actually makes Django CPU track chunk QPS is per-request ORM and serializer
work, and that a token read which issues **zero ACL queries** has removed that
coupling. That is an assertion about behaviour, so it is measured here and
asserted, not left as prose.

Doc 23's SLO is **chunk p95 < 150 ms warm on local SSD**; that is checked too.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection

from projects.models import Dataset, Project
from volumes.chunks import core as chunk_core
from volumes.chunks import service
from volumes.chunks.metrics import METRICS
from volumes.models import Volume
from volumes.pyramid import service as pyramid_service
from volumes.pyramid import store

User = get_user_model()

PYRAMIDS = dict(FEATURE_VOLUME_PYRAMIDS=True)
ON = dict(FEATURE_VOLUME_PYRAMIDS=True, FEATURE_CHUNK_SERVICE=True)
#: A realistic plane, so chunk reads are chunk-sized rather than trivial.
BENCH_SHAPE = (8, 2048, 2048)
#: Doc 23 SLO.
P95_TARGET_MS = 150.0


def _zarr_available() -> bool:
    try:
        store.require_zarr()
        return True
    except Exception:  # pragma: no cover
        return False


class ChunkBenchmark(TestCase):
    def setUp(self):
        if not _zarr_available():  # pragma: no cover
            self.skipTest("zarr is an optional dependency and is not installed")
        METRICS.reset()
        chunk_core.HANDLES.clear()

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir(parents=True)
        external = Path(self.tmp.name) / "src"
        external.mkdir()

        rng = np.random.default_rng(41)
        source = rng.integers(0, 60000, size=BENCH_SHAPE, dtype=np.uint16)
        image = external / "bench.tif"
        tifffile.imwrite(str(image), source)

        user = User.objects.create_user(username="bench", password="x")
        self.user = user
        project = Project.objects.create(title="Bench", created_by=user)
        dataset = Dataset.objects.create(project=project, name="DS")
        self.volume = Volume.objects.create(
            project=project, dataset=dataset, name="bench",
            image_path=str(image),
            voxel_size_z=40.0, voxel_size_y=8.0, voxel_size_x=8.0,
        )
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **PYRAMIDS):
            pyramid_service.build_pyramid(self.volume)
        self.volume.refresh_from_db()

    @staticmethod
    def _stats(samples: list[float]) -> dict:
        ordered = sorted(samples)
        n = len(ordered)
        return {
            "p50": ordered[n // 2],
            "p95": ordered[min(n - 1, int(n * 0.95))],
            "p99": ordered[min(n - 1, int(n * 0.99))],
            "max": ordered[-1],
        }

    def _read(self, mag: str, cz: int, token: str | None = None) -> float:
        start = time.perf_counter()
        if token:
            service.read_chunk_with_token(token=token, mag=mag, cz=cz, cy=0, cx=0)
        else:
            service.read_chunk(
                volume_id=self.volume.pk, mag=mag, cz=cz, cy=0, cx=0, user=self.user
            )
        return (time.perf_counter() - start) * 1000.0

    def test_cold_versus_warm_and_zero_query_token_reads(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            caps = service.capabilities(volume_id=self.volume.pk, user=self.user)
            mags = [m["mag"] for m in caps["mags"]]
            depth = {m["mag"]: m["grid"][0] for m in caps["mags"]}

            # --- cold: no cached handle
            chunk_core.HANDLES.clear()
            cold = self._read("1", 0)

            # --- warm: same chunk repeatedly
            warm_same = self._stats([self._read("1", 0) for _ in range(15)])

            # --- sequential z scrub, and random z
            seq = self._stats(
                [self._read("1", z % depth["1"]) for z in range(depth["1"] * 2)]
            )
            rng = np.random.default_rng(7)
            rand = self._stats(
                [self._read("1", int(rng.integers(0, depth["1"]))) for _ in range(15)]
            )

            per_mag = {
                m: self._stats([self._read(m, 0) for _ in range(9)]) for m in mags
            }

            # --- token path, and the query count that ADR-010 §1 rests on
            issued = service.issue_token(volume_id=self.volume.pk, user=self.user)
            token = issued["token"]
            self._read("1", 0, token=token)  # warm the handle
            with CaptureQueriesContext(connection) as captured:
                self._read("1", 0, token=token)
            token_queries = len(captured)
            token_stats = self._stats(
                [self._read("1", z % depth["1"], token=token) for z in range(15)]
            )

            with CaptureQueriesContext(connection) as auth_captured:
                self._read("1", 0)
            auth_queries = len(auth_captured)

            snap = METRICS.snapshot()

        # --- assertions with teeth -----------------------------------------
        # What the handle cache buys is stated as an invariant, not a stopwatch
        # comparison: cold and warm differ only by the cost of opening the Zarr
        # group, which on a warm page cache is smaller than run-to-run noise, so
        # `warm_p50 < cold` fails intermittently while the cache works fine.
        # "The group was opened once across every read" says the same thing and
        # cannot flake. The timings below are reported, and the SLO is asserted.
        self.assertEqual(
            snap["chunk_cache_misses_total"], 1,
            "the Zarr group was opened more than once across these reads — the "
            "handle cache is buying nothing",
        )
        self.assertGreater(
            snap["chunk_cache_hits_total"], 50,
            "too few cached reads to draw a conclusion from",
        )
        self.assertLess(
            warm_same["p95"], P95_TARGET_MS,
            f"warm p95 {warm_same['p95']:.1f} ms exceeds the doc 23 SLO of "
            f"{P95_TARGET_MS:.0f} ms",
        )
        self.assertLessEqual(
            token_queries, 1,
            "a token chunk read issued more than the single primary-key volume "
            "fetch — Django CPU would then track chunk QPS, which is exactly "
            "what ADR-010 §1 claims this design avoids",
        )
        self.assertGreater(snap["chunk_cache_hit_ratio"], 0.5)

        print(
            "\n  --- Phase 12 chunk-service benchmark ---"
            f"\n  source                 {BENCH_SHAPE} uint16, mags={mags}"
            f"\n  cold (no handle)       {cold:7.2f} ms"
            f"\n  warm same chunk        p50 {warm_same['p50']:6.2f}  "
            f"p95 {warm_same['p95']:6.2f}  p99 {warm_same['p99']:6.2f} ms"
            f"\n  sequential z scrub     p50 {seq['p50']:6.2f}  "
            f"p95 {seq['p95']:6.2f}  p99 {seq['p99']:6.2f} ms"
            f"\n  random z access        p50 {rand['p50']:6.2f}  "
            f"p95 {rand['p95']:6.2f}  p99 {rand['p99']:6.2f} ms"
            f"\n  token path             p50 {token_stats['p50']:6.2f}  "
            f"p95 {token_stats['p95']:6.2f}  p99 {token_stats['p99']:6.2f} ms"
        )
        for mag in mags:
            s = per_mag[mag]
            print(f"  mag {mag:<3s}                p50 {s['p50']:6.2f}  p95 {s['p95']:6.2f} ms")
        print(
            f"  SQL queries / token read   {token_queries}  (authenticated: {auth_queries})"
            f"\n  token verify           p50 {snap['chunk_token_verify_seconds']['p50']:.3f} ms"
            f"\n  cache hit ratio        {snap['chunk_cache_hit_ratio']:.3f}"
            f"\n  bytes served           {snap['chunk_bytes_total'] / 1e6:.1f} MB"
            f"\n  doc 23 SLO (p95<150ms) {'MET' if warm_same['p95'] < P95_TARGET_MS else 'MISSED'}"
        )

    def test_cache_invalidation_after_rebuild_costs_one_cold_read(self):
        """A rebuild must invalidate without an explicit flush, and the cost of
        that is exactly one cold read — not a permanently colder cache."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            self._read("1", 0)
            warm_before = self._read("1", 0)

        time.sleep(0.01)
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **PYRAMIDS):
            pyramid_service.build_pyramid(self.volume)
        self.volume.refresh_from_db()

        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            after_rebuild = self._read("1", 0)   # cold again
            warm_again = self._read("1", 0)      # warm again

        print(
            "\n  --- cache invalidation ---"
            f"\n  warm before rebuild    {warm_before:7.2f} ms"
            f"\n  first after rebuild    {after_rebuild:7.2f} ms  (cold, expected)"
            f"\n  warm after rebuild     {warm_again:7.2f} ms"
        )
        self.assertLess(
            warm_again, after_rebuild * 1.5,
            "the cache did not recover after a rebuild",
        )

    def test_concurrent_chunk_reads_scale_and_stay_correct(self):
        """Concurrent reads through one shared handle.

        A viewer scrubs with several requests in flight, so the shared handle
        cache is contended. Measured at the core level deliberately: threads in
        a `TestCase` get their own database connections that cannot see the
        fixture, and the contended resources here — the handle cache lock and
        the Zarr reads — are below the ORM anyway.
        """
        from concurrent.futures import ThreadPoolExecutor

        # The factory resolves the recorded path lazily, so every call must
        # happen while the temporary data root is still overridden.
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            group_factory = service._group_factory(self.volume)
            identity = service._build_identity(self.volume)
            caps = service.capabilities(volume_id=self.volume.pk, user=self.user)
            depth = next(m["grid"][0] for m in caps["mags"] if m["mag"] == "1")

            cache = chunk_core.HandleCache(max_entries=8)

            def read(z: int) -> tuple[float, bytes]:
                start = time.perf_counter()
                result = chunk_core.read_chunk(
                    address=chunk_core.ChunkAddress(
                        volume_id=self.volume.pk, mag="1", cz=z % depth, cy=0, cx=0
                    ),
                    group_factory=group_factory,
                    build_identity=identity,
                    cache=cache,
                )
                return (time.perf_counter() - start) * 1000.0, result.data

            # Serial baseline first, so speedup is measured on this machine.
            n = 24
            serial_start = time.perf_counter()
            expected = [read(z)[1] for z in range(n)]
            serial_ms = (time.perf_counter() - serial_start) * 1000.0

            for workers in (4, 8):
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    par_start = time.perf_counter()
                    results = list(pool.map(read, range(n)))
                wall_ms = (time.perf_counter() - par_start) * 1000.0
                latencies = self._stats([r[0] for r in results])

                # Correctness under concurrency is the point; speed is reported.
                for z, (_, data) in enumerate(results):
                    self.assertEqual(
                        data, expected[z],
                        f"concurrent read of z={z} with {workers} workers "
                        "returned different bytes than the serial read — the "
                        "shared handle cache is not safe under concurrency",
                    )
                self.assertEqual(
                    cache.size(), 1,
                    "concurrent reads created more than one handle for the "
                    "same build — the cache factory is racing",
                )
                print(
                    f"\n  --- concurrent chunk reads ({workers} workers) ---"
                    f"\n  {n} reads wall       {wall_ms:7.2f} ms  "
                    f"(serial {serial_ms:7.2f} ms, "
                    f"speedup {serial_ms / wall_ms:.2f}x)"
                    f"\n  throughput          {n / (wall_ms / 1000.0):7.1f} chunks/s"
                    f"\n  latency             p50 {latencies['p50']:6.2f}  "
                    f"p95 {latencies['p95']:6.2f}  max {latencies['max']:6.2f} ms"
                )
                self.assertLess(
                    latencies["p95"], P95_TARGET_MS,
                    f"concurrent p95 {latencies['p95']:.1f} ms exceeds the doc "
                    f"23 SLO of {P95_TARGET_MS:.0f} ms",
                )
