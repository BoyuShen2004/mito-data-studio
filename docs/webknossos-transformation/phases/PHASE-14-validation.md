# Phase 14 — validation

**Starting HEAD:** `4a5dcc418d239199cb73a15aad2c80716d4dc53f`
**Branch:** `phase14-rendering-navigation` (to be fast-forwarded locally onto
`webknossos-transformation`)
**Production deployment:** not touched

## Scope and gate

The authoritative title is **Rendering/nav redesign** and the formal phase-map
gate is **UI familiarity check**. ADR-012 preserves the established Canvas2D
proofreading UI and all annotation/write semantics. The Phase 14 flag changes
only the source-image adapter. Quantitative supporting gates come from target
rendering doc 21.

## Implemented evidence

- Real `SliceViewer` and `AnnotationCanvas` can select a chunk-backed image
  source behind `VITE_FEATURE_CHUNK_RENDERER=false`.
- The TIFF/PNG source remains unchanged and is used while disabled, for public
  token surfaces, or after a visible one-way session fallback.
- Pure XY/XZ/YZ assemblers use absolute chunk offsets and request only the
  intersecting chunk slab. Analytic `(z,y,x)` fixtures prove orientation and
  cropped edges.
- All Phase 12 numeric dtypes use one stable volume metadata display window.
  Unknown dtype/endianness/shape/size remains rejected by the strict client.
- Rapid navigation may show a source-sized coarse frame; a complete,
  generation-valid fine frame atomically replaces it.
- Image-layer substitution does not alter overlay dimensions or the existing
  `voxelFromSlice`, tool, Save, autosave, recovery, undo or label APIs.
- Native `window.fetch` is invoked through a receiver-safe wrapper. A real
  Chromium route test caught and now prevents the `Illegal invocation` failure
  that mocked transports could not expose.

## Tests and builds

| Gate | Result |
|---|---:|
| Django system check | pass |
| Django deploy check | pass with the six documented local non-TLS warnings |
| `makemigrations --check --dry-run` | no changes |
| `migrate --plan` | only the two documented unapplied pre-existing migrations |
| Phase 11/12 real-Zarr suite | 128 passed |
| Complete PostgreSQL backend suite | 1,222 passed in 1,148.846 s |
| Frontend Vitest suite | 145 passed |
| Phase 14 rendering/chunk focused suite | 33 passed |
| TypeScript | pass |
| Production build | pass |
| Flag builds: off/off, PullQueue/on, renderer/on | pass |
| Chromium Playwright | 5 passed |

Fresh detached checkout at `cfa15b7` independently passed Django checks,
migration checks, the 128-test Phase 11/12 suite (123.796 s), TypeScript,
145 frontend tests, production build and all five Playwright tests after a new
`npm ci`. It did not depend on files from the implementation worktree.

## Browser performance

Chromium uses precise-memory reporting and the real Canvas2D paint handoff.
The analytic fixture prevents orientation errors from being hidden by
screenshots.

| Case | p50 | p95 | p99 |
|---|---:|---:|---:|
| 512² warm chunk assemble/convert/paint | 15.5 ms | 16.0 ms | 34.3 ms |
| 2048² full-frame assemble/convert/paint | 95.2 ms | 163.4 ms | 163.4 ms |

The 512² representative viewport clears the official warm scrub p95 target of
100 ms. The 2048² number is reported as a full-frame stress measurement and is
not substituted for a tiled viewport claim.

Same-host decoded PNG versus chunk handoff at 512²:

- PNG p50/p95: 16.7/16.7 ms; encoded payload 64,494 bytes.
- chunk p50/p95/p99: 15.0/15.7/15.9 ms.
- This controlled comparison isolates browser decode/assembly/paint. It is not
  represented as a network or production-data benchmark.

## Memory soak

The sustained browser test performs 1,800 rendered frames while alternating
axes and indices. Warm-up and final retained heap are measured after identical
CDP garbage collection:

- before: 1,912,928 bytes;
- after: 1,929,983 bytes;
- growth: **0.89%** (gate: no more than 15%);
- canvas elements after soak: one.

Unit/mounted tests separately prove queue disposal and request cancellation on
unmount. The authoritative phase map assigns the two-hour/multi-user production
soak to the later load-and-soak phase; Phase 14 does not start that phase.

## Browser production-component path

Playwright mounts the actual `/viewer/volumes/7` route with the Phase 14 flag,
intercepts disposable analytic API data, and verifies:

1. authenticated user and volume metadata;
2. capability discovery;
3. token acquisition;
4. signed little-endian chunk validation;
5. visible Canvas-backed image dimensions;
6. Coronal axis navigation and pixel dimensions;
7. no user-visible TIFF fallback.

React StrictMode's development-only effect probe may start superseded TIFF
requests; generation identity and a latest-viewport retry prevent them from
committing or switching the session. The production build does not double-run
effects. The production component axis test passed ten repeated runs.

## Explicit exclusions and remaining release work

- No backend model, migration or API change was required.
- No persistent cache, WebGL rewrite, mesh work, triple-view UI, production
  migration or deployment was performed.
- The same-host benchmark uses generated data; production-sized same-LAN
  network and two-hour/multi-user soak remain release/load-test work.
- Legacy builds keep the feature disabled by default. As of 2026-08-04 the
  `production_integrated_v1` production build enables it for streaming-ready
  volumes; non-ready and failed sessions retain the source-slice fallback.
