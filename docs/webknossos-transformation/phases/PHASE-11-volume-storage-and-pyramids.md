# Phase 11 — volume storage & pyramids

**Status:** **complete.** The format is locked by ADR-009 and pinned by a writer,
a reader and checksum tests.
**Depends on:** Phase 0 · **Gate:** **format decision locked**
**Design:** [ADR-009](../adr/ADR-009-volume-storage-and-pyramids.md) · [scope note](PHASE-11-scope-note.md)

---

## What shipped

| Area | Detail |
|---|---|
| Pure core | `volumes/pyramid/ladder.py` — anisotropy-aware mag ladder; `downsample.py` — block reduction + slab planning. Neither imports Django or zarr |
| Store | `store.py` — the Zarr v3 contract, placement, promote-after-validate |
| Validation | `validate.py` — deterministic random-chunk checksums against the source |
| Service | `service.py` — orchestration, flag gate, readiness, rollback |
| Model | `Volume.ready_streaming`, `Volume.pyramid_metadata`; `ProcessingJobType.BUILD_PYRAMID` |
| Migrations | `volumes.0005`, `processing.0002` — additive, both apply **and** unapply |
| Flag | `FEATURE_VOLUME_PYRAMIDS`, default **False** |
| Dependency | `zarr>=3.1,<4` (MIT), declared in `environment.yml`, **imported lazily** |

## Completion gate — evidence

| Gate item | Evidence |
|---|---|
| Format locked in an ADR with rejected alternatives | ADR-009 §2–§5 |
| A writer produces it, a reader reads it back exactly | mag 1 round-trips the source; `zarr_format: 3` asserted on disk |
| Random chunk checksums validate; corruption is detected | 12 chunks/build; a deliberately corrupted chunk fails the build |
| `ready_streaming` flips only after validation | asserted on both the success and failure paths |
| Build bounded and idempotent | peak slab = one plane; rebuild digest stable |
| Flag off ⇒ existing behaviour byte-identical | refuses, writes nothing, volume stays not-ready |
| Source images byte-identical after a build | bytes **and** mtime asserted |
| Benchmarks recorded | below |

## Benchmark

4 × 2048 × 2048 uint16 (33.6 MB), 3 levels:

| Metric | Value |
|---|---|
| Build wall time | 1130 ms |
| Peak resident slab | 4,194,304 voxels — **exactly one plane**, not the volume |
| Bytes written | 43.8 MB in 88 files (1.30× source) |
| Chunks validated | 12 (seed 20260730) |
| Read mag 1 / 2 / 4 | 14.69 / 5.46 / **3.18 ms** p50 — mag 4 is **4.6× cheaper** |
| Store open | 5.49 ms per call |

**Plane size decides whether the pyramid appears to help.** At 512² planes a
mag-4 read is only ~0.78× a mag-1 read, because zarr's fixed per-read cost
dominates 512 KB of data; at 2048² it is ~0.14×. The benchmark therefore uses a
realistic plane — measuring the small case and reporting "the pyramid barely
helps" would have been a true sentence about an unrepresentative size.

## Validation

| Check | Result |
|---|---|
| Full PostgreSQL backend suite, clean DB | **1127 OK** |
| Phase 11 focused (core + build + bench) | **58 OK** |
| Smoke matrix, **18 flag configurations** | **91 OK** |
| Migrations forward and reverse | apply / unapply / re-apply |
| Fresh **empty** database from zero | both columns present |
| Representative **legacy** data | pre-Phase-11 rows survive: not ready, empty metadata, `image_path` intact |
| Backend startup | WSGI loads; flag defaults False |
| Backend startup **with zarr absent** | app loads; `require_zarr` raises a clear error |
| Fresh isolated Python 3.11 checkout | **252 OK**, checks clean, no pending migrations |
| Frontend (unchanged by this phase) | typecheck 0, 78 tests, build ✓ — in tree *and* fresh checkout |
| Real dev data root writes | **0** |

## Bugs found and fixed

1. **Validation rejected correct derivatives.** Levels are built successively
   from the parent, but validation recomputed in one step from the source, and
   mean-of-means ≠ a single mean once integer rounding is involved. Fixed by
   modelling the same successive chain — deliberately *not* by loosening the
   comparison to a tolerance, which is how a checksum stops being a checksum.
2. **Two volumes sharing an image basename collided** on one pyramid path. Now
   reuses `working_mask_stem`, which already carries the `_v<id>` rule.
3. **The benchmark measured store-open latency wearing a read's name.**
   `read_plane` opens the group per call; separated and both reported.

## Known limitations before Phase 12

> Activation update (2026-08-04): `production_integrated_v1` now enables the
> flag. Registration auto-enqueues a durable build, the local dispatcher runs
> the real builder, and managers have an authenticated HTTP/UI Build/Rebuild
> control. The historical limitations below describe the original Phase 11
> delivery, not the integrated production surface.

- **Nothing reads the derivative yet.** The editor still reads the source TIFF
  through `slice_io`; switching the read path is Phase 13–14.
- **`store.read_plane` opens the group per call** (~5.5 ms). Fine for a
  build-side helper; Phase 12 serves chunks in a loop and should cache the
  handle rather than inherit this.
- **No job-runner integration.** `ProcessingJobType.BUILD_PYRAMID` exists and
  the service is callable, but no adapter wires it to Slurm/local execution —
  doc 20 names the job, and running it at scale belongs with the chunk service.
- **No HTTP trigger endpoint.** Deliberate: chunk serving is row 12, and a
  convenience endpoint here would pre-empt its authz design.
- **Compression is zarr's default codec pipeline**, not tuned. Measured 1.30×
  the source on random data, which is close to worst case; real EM will compress
  far better, but no tuning was attempted.
- **Anisotropy tolerance is a constant** (1.5), not per-dataset configurable.
- **`webknossos.Dataset` remains unused** on licensing grounds, so any future
  need for WKW/N5 interop would be a fresh decision.
