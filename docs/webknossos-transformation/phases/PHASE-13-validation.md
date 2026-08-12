# Phase 13 — validation

**Starting commit:** `732d07a7f74783a4a44f274b4e8a85b97afd86fc`

## Baseline and WIP protection

- Branch confirmed: `webknossos-transformation`.
- The starting development tree had 78 documented pre-existing WIP paths and
  no staged files.
- Backup/evidence:
  `/home/weidf/shenb/mito-phase13-baseline-20260731T040240Z/`
  contains the starting status, binary patch, untracked inventory and 78
  copied WIP files (no `.env` or secrets).
- Phase 13 was developed on isolated branch `phase13-pullqueue` in
  `/home/weidf/shenb/mito-data-studio-phase13-20260731T040240Z`.
- No Phase 13 implementation file overlapped the documented dirty paths.
- The authoritative development working-tree status hash remained
  `4ddb7f1f0d560918c9f1b3a80443fd07e8149415698720442ed394a5edaa254b`
  before landing.

## Backend

| Gate | Result |
|---|---|
| Django system check | pass, 0 issues |
| Django deploy check | pass with the existing six local TLS/DEBUG warnings |
| `makemigrations --check --dry-run` | pass, no changes |
| `migrate --plan` | pass; development DB remains two already-committed integration migrations behind and was not changed |
| Fresh empty PostgreSQL migration | pass through every migration; disposable database dropped |
| Phase 11/12 real-Zarr focused suite | **128 passed**, 123.994 s |
| Complete PostgreSQL suite | **1222 passed**, 1144.154 s |

The shared validation interpreter did not have optional zarr installed, so the
first baseline correctly skipped 96 Zarr-dependent tests. A separate dependency
directory installed `zarr==3.1.6` without changing the deployment venv; the
focused and full final runs then executed those tests rather than counting
skips. Full-suite migration/backfill tests provide representative legacy-data
coverage.

No model changed and Phase 13 has zero migrations. The only backend API change
is additive `build_identity` metadata required for correct client invalidation.

## Frontend

| Gate | Result |
|---|---|
| TypeScript `tsc --noEmit` | pass |
| Vitest | **125 passed** |
| Phase 13 queue/client/cache/adapter/benchmark tests | **42 passed** |
| Existing mounted annotation/autosave tests | pass within the 125 |
| Production Vite build | pass, 115 modules |
| `git diff --check` | pass |

Fresh detached checkout
`/home/weidf/shenb/mito-data-studio-phase13-verify-20260731T043743Z` at
`3535ad8309ac14684271cdca3a6f51eb026e28f6` independently passed Django
checks, migration drift checks, the 128-test real-Zarr Phase 11/12 suite,
TypeScript, all 125 frontend tests and the production build. Its only
untracked entry during validation was the deliberate `node_modules` symlink;
no code or fixture was copied from a dirty tree.

The bundle-size warning (>500 KiB) is pre-existing and non-fatal. Phase 13 does
not add a production route or mount a renderer, so Playwright/browser rendering
is not an acceptance claim for this phase. The mounted React harness validates
adapter lifecycle and unmount cancellation; Phase 14 owns actual canvas render
handoff and its browser-driven same-LAN gate.

## Scrub benchmark

The final controlled run covers 512² and 2048² planes at mags 1/2/4, cold and
warm sequential scrub, random jumps, reversal, duplicate consumers and
concurrency caps 1/3/6/12. It uses the production queue, token provider, strict
`Response` parser, typed-array decode, byte LRU and slice assembler.

- worst warm p95: **28.16 ms** (2048², mag 1), below the 100 ms gate;
- 2048² mag-1 warm p50/p95/p99: **18.79 / 28.16 / 28.16 ms**;
- concurrency sweep: **28.45 / 17.98 / 11.47 / 12.89 ms** at 1/3/6/12;
- default six-way concurrency is supported by measurement;
- dedupe, cancellations, queue wait, network, decode, token refresh, cache hit
  ratio and retained bytes are emitted in the machine-readable benchmark log;
- 2048² mag-1 retained about 91 MiB under the benchmark's 160 MiB budget;
  production default is 64 MiB.

These are controlled scheduler/client numbers. The real Phase 0 TIFF/JPEG liver
baseline remains 659.2 ms p95; Phase 14 must measure browser render handoff on
the same LAN rather than treating jsdom as a browser.

## Smoke and correctness

- all flags off: existing TIFF/PNG path;
- Phase 11 alone and Phase 12 combinations: existing backend smoke matrix;
- Phase 13 flag off: factory returns no adapter and performs no request;
- Phase 13 enabled without 11/12: typed 503, no unrelated fallback;
- minimum dependency set: adapter initializes and returns exact pixels;
- autosave/recovery combinations: unchanged mounted editor suite passes;
- malformed headers/dtype/shape/offset/build/size: fail closed;
- pyramid rebuild: old in-flight consumers cancel before cache clear;
- volume/deployment/auth scope: encoded in every request/cache key;
- signed tokens: memory only, header only, refresh-collapsed and never cached;
- HTTP cache: explicitly bypassed because Phase 12 URLs are not build-versioned;
- source TIFF and annotation write paths: unmodified.

## External-state proof

- Real development data root: zero files newer than the Phase 13 start marker.
- Live deployment HEAD remains
  `79ad5d9a47047484ff90b59868a22186a22d90be`.
- Live deployment porcelain-v2 hash remains
  `c78c33ac3fbb2e3977d989a18f0d4dfc730098334e143b2cecc58eb44610bcdb`.
- All 71 deployment source hashes still pass the audit manifest.
- Production data remains 17,293 files / 46,150,386,345 bytes with the same
  size/mtime fingerprint
  `5e0cb6c48ebf696060135942060ced7351aefde21e4cfae377627f2a90ce5731`.
- Gunicorn master remains PID 2935215, started
  `Wed Jul 29 13:48:30 2026`; port 18188 returns HTTP 200 / 400 bytes.
- Retired deployment HEAD and porcelain hash remain
  `83b547c3b16b6104cc53bad8c795c4fcbeab8fa1` and
  `671eb3addd972b2144200d144c1216fa91ded0cb094634c7ffbf5597b868675a`.
- No service signal, restart, deploy, push, production migration or production
  data operation was issued.

## Phase 14 handoff

Phase 14 receives the public `features/chunks` adapter seam. It owns mounting it
into the familiar viewer, raw-dtype window/level conversion, XZ/YZ assembly,
coarse-to-fine visual replacement, browser rendering tests and same-LAN/soak
measurements. The TIFF path must remain available until that flag-on browser
matrix is accepted.
