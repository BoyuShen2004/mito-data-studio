# Phase 13 — frontend chunk cache/scheduler

**Status:** complete
**Design:** [ADR-011](../adr/ADR-011-frontend-chunk-cache-and-scheduler.md) ·
[scope note](PHASE-13-scope-note.md)

## Delivered

| Area | Implementation |
|---|---|
| Request identity | Collision-safe key includes deployment, volume, pyramid build, mag, z/y/x chunk, dtype/representation and authorization scope |
| PullQueue | Stable four-class priority, bounded global/per-volume concurrency, fairness, dedupe, independent consumer cancellation, reprioritization, bounded retry/backoff, generation cancellation/stale rejection and disposal |
| Memory cache | Deterministic byte-bounded LRU; default 64 MiB; hit/miss/eviction instrumentation |
| Chunk client | Authenticated and signed reads, raw little-endian typed-array decode, exact metadata/address/build validation, edge chunks, response cap, HTTP-cache bypass and typed errors |
| Tokens | Memory only, expiry skew, concurrent refresh collapse, one refresh after 401/403, caller-local cancellation, provider disposal, header transport (never query-string/persistent storage) |
| Data source | Framework-independent XY slice assembly with coarse-during-motion selection, fine refinement and direction-sensitive bounded prefetch |
| Metrics | Cache, dedupe, cancel, stale, retry, token refresh, eviction, queue wait, network and decode timings |
| Flag | `VITE_FEATURE_CHUNK_PULL_QUEUE=false` unless explicitly enabled |
| Backend addition | `build_identity` in capabilities and `X-Mito-Build-Identity` on reads; no model or migration |

The existing TIFF/PNG viewer and AnnotationCanvas were not modified. Source
images remain read-only and the existing label Save/autosave/recovery path is
the only write path.

Each adapter owns a scoped queue unless Phase 14 injects an app-wide queue.
Caller-owned queues survive one viewer unmount; only that adapter's consumer
handles are cancelled.

Chunk reads deliberately use fetch `cache: "no-store"`. Phase 12's URLs do not
carry build identity (and the signed URL does not carry volume identity), so
its otherwise useful `immutable` header cannot safely key browser storage.
The Phase 13 LRU has the complete safe key; a future versioned URL can restore
an HTTP-cache layer.

## Scrub policy

- `CURRENT` work always outranks `REFINE`, `NEAR` and `PREFETCH`.
- Each viewport update advances a generation and cancels older consumers.
- Duplicate chunks share one transport request.
- While moving, the first available mag ≥2 is the useful slice; mag 1 is queued
  as refinement. At rest, the finest mag is current.
- Directional neighbors are queued ahead of one opposite-direction neighbor.
  Reversal changes that set on the next generation. Indices are clipped at the
  volume boundary.
- A result carries its actual mag and build identity. An old generation rejects
  rather than returning pixels to display code.

## Test matrix

| Behavior | Automated evidence |
|---|---|
| Concurrency and per-volume fairness | `pullQueue.test.ts` |
| Stable priority / no prefetch inversion | `pullQueue.test.ts` |
| Dedupe and independent cancellation | `pullQueue.test.ts`, `chunkDataSource.test.tsx` |
| Abort, retry, reprioritize, disposal | `pullQueue.test.ts` |
| Out-of-order stale generation | `pullQueue.test.ts` |
| Byte LRU hit/eviction/clear | `byteLru.test.ts` |
| Strict dtype/byte order/shape/address/build | `chunkClient.test.ts` |
| Edge chunk and response limit | `chunkClient.test.ts` |
| Token refresh collapse/expiry retry/cancellation | `chunkClient.test.ts` |
| Exact assembled pixels | `chunkDataSource.test.tsx` |
| Coarse/refine, repeated slice, boundary | `chunkDataSource.test.tsx` |
| Rebuild invalidation | frontend adapter test + backend chunk contract test |
| Deployment/volume/auth isolation | identity and adapter tests |
| Flag off / missing dependencies / enabled adapter | mounted integration-seam tests |
| Unmount cleanup | mounted React harness |
| Save/autosave/source immutability | unchanged Phase 10 mounted editor and Phase 11/12 source-byte tests |

## Benchmark

Command:

```bash
npm run bench:phase13 --prefix frontend
```

It drives the production PullQueue, token provider, strict `Response` parser,
decoded cache and XY assembler through a controlled same-origin transport with
a nonzero delay. It is intentionally not described as a browser/LAN/render
benchmark: Phase 13 does not mount a renderer. The Phase 0 real TIFF baseline
for the representative liver volume remains **659.2 ms p95**.

Representative validation run:

| Plane | Mag | cold p95 (ms) | warm p95 (ms) | random p95 (ms) | reverse p95 (ms) |
|---:|---:|---:|---:|---:|---:|
| 512² | 1 | 9.14 | 7.50 | 3.01 | 1.40 |
| 512² | 2 | 2.68 | 0.48 | 1.64 | 0.71 |
| 512² | 4 | 5.08 | 0.25 | 1.39 | 0.21 |
| 2048² | 1 | 49.64 | **28.16** | 29.67 | 36.95 |
| 2048² | 2 | 17.11 | 2.42 | 7.66 | 5.13 |
| 2048² | 4 | 2.85 | 1.19 | 5.52 | 0.49 |

The controlled warm p95 gate is met in all six cases. The 2048² mag-1 case
retained about 91 MiB under a 160 MiB benchmark budget. The production default
is 64 MiB. Metrics also recorded queue wait, network/decode time, deduplicated
requests and cancellation of obsolete prefetch. A 2048² cold-plane concurrency
sweep measured 28.45 / 17.98 / 11.47 / 12.89 ms at caps 1 / 3 / 6 / 12; six is
therefore the default rather than assuming more concurrency is always faster.

The requested path comparison is deliberately labelled by environment:

| Path | Result | Interpretation |
|---|---:|---|
| Existing TIFF/JPEG path | 659.2 ms p95 | Phase 0 real 17.7 MPix liver baseline |
| Naive chunk client | 27.78 ms | One controlled cold 2048² mag-1 plane, serial chunks |
| PullQueue cold scrub | 49.64 ms p95 | Controlled sequence including scheduling/prefetch churn |
| PullQueue + decoded cache | 28.16 ms warm p95 | Controlled realistic scrub; Phase 13 gate |

The controlled rows isolate scheduler/client behavior and are not presented as
same-LAN browser-render numbers. Their value is the comparison, cache/cancel
instrumentation and repeatable regression gate.

## Smoke matrix

| Pyramids | Chunk service | Phase 13 flag | Result |
|---|---|---|---|
| off | off | off | Existing TIFF path only |
| on | off | off | Phase 11 derivative only; TIFF path |
| off | on | off | Phase 12 reports disabled; TIFF path |
| on | on | off | Phase 12 available; TIFF path |
| off | off | on | Adapter initialization fails closed with typed 503 |
| on | on | on | Adapter initializes and serves chunk-backed XY slices |
| any supported annotation flags | on | on | Adapter is read-only; existing save/autosave path is unchanged |

Backend combinations are pinned by `annotation.test_smoke_matrix`; the frontend
off/dependency/enabled cases are pinned by `chunkDataSource.test.tsx`.

## Phase 14 handoff

Phase 14 may mount `ChunkDataSource` into the familiar viewer. It must decide:

1. Canvas conversion/render handoff for typed intensity planes;
2. XZ/YZ assembly and progressive visual fallback;
3. when coarse pixels are replaced with fine pixels without a visual jump;
4. window/level mapping for raw dtype values;
5. browser-driven same-LAN TTFV, render-handoff and 2-hour heap measurements.

It must not remove the TIFF path until the flag-on browser matrix is accepted.

Activation update (2026-08-04): `npm run build:production` compiles the
PullQueue on. It is selected only for `ready_streaming` volumes, while the
original TIFF/HDF5/NIfTI path remains the visible one-way fallback.
Annotation label loading and all write paths remain separate from this
read-only source adapter.
