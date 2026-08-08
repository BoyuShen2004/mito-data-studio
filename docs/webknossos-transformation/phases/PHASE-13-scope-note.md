# Phase 13 — scope note

**Official title:** **Frontend chunk cache/scheduler**
**Depends on:** Phase 12 (which depends on Phase 11)
**Gate:** **p95 scrub target** — `< 100 ms` after warmup
**Design record:** [ADR-011](../adr/ADR-011-frontend-chunk-cache-and-scheduler.md)

## Required

- framework-independent, bounded and observable PullQueue;
- stable priorities, deduplication, coalescing, cancellation, retry and stale
  generation rejection;
- strict binary client for Phase 12 authenticated and signed-token reads;
- memory-only token lifecycle with collapsed refresh;
- byte-bounded decoded-chunk LRU isolated by deployment, volume, pyramid build,
  magnification, representation and authorization scope;
- direction-sensitive scrub planning and bounded prefetch;
- XY chunk-backed slice-data-source adapter;
- disabled-by-default `VITE_FEATURE_CHUNK_PULL_QUEUE`;
- mounted adapter tests and representative scrub benchmarks;
- additive build-identity response metadata required for safe invalidation.

## Explicitly excluded

- replacing the TIFF/PNG path in SliceViewer or AnnotationCanvas;
- changing annotation labels, save, autosave, recovery or source ownership;
- WebGL, navigation redesign, XZ/YZ chunk assembly, meshes or GPU residency;
- persistent browser caches;
- chunk write APIs or sparse annotation storage;
- a new backend process, database model or migration.

These are Phase 14 or later. With the flag off, current editor behavior is
byte-for-byte the existing path.

## Completion evidence

All queue/client/cache/adapter tests pass; the existing annotation workspace
tests remain green; malformed data fails closed; a rebuilt pyramid and a
volume/deployment switch cannot reuse stale memory; benchmarks exercise real
queue/client/decode code and meet the controlled warm p95 gate. Browser render
handoff remains a Phase 14 acceptance item because Phase 13 deliberately does
not mount a new renderer.
