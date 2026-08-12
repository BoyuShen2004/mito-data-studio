# RC1 release and staging validation

Validated application/config commit: `f8a7d3f22c51308b0ab19cdca661910d705d2c6b`.
Subsequent branch-tip changes contain this evidence only. Parent branch:
`webknossos-transformation`. Local release branch:
`release/mito-data-studio-2026-07-31-rc1`. The annotated tag is deliberately
not created: the two-hour/memory gate and real chunk-renderer latency gate are
not green.

## Release contents and WIP closure

All 78 frozen WIP paths were read and resolved as recorded in
`2026-07-31-wip-resolution.md`: 31 application/UI paths (A-C), 16 release or
operations documentation paths (D), and 31 research/history paths (E). There
are no F/G/H leftovers and no release dependency on an uncommitted path. The
source and staging checkouts are clean. Runtime state, secrets, builds, model
data, database backups and real images/masks are excluded.

## Isolated staging identity

- checkout: `/home/weidf/shenb/mito-data-studio-staging-20260731`
- bind: `127.0.0.1:18189`
- PostgreSQL: dedicated PG16 container/database `mito-staging-postgres-20260731` / `mito_staging`
- data root: `/home/weidf/shenb/mito-data-studio-staging-20260731/data`
- runtime user: `mito-staging`; dedicated Python 3.11.14 venv and frontend build
- restored rows after three staging-only test users: users 9, projects 1,
  datasets 3, volumes 3, tasks 3, task instances 3, submissions 1, reviews 1,
  hard cases 7. Six staging-only annotation operations were created by the
  Save and concurrency checks.
- external source/reference data is shared only read-only; application source
  TIFF opens use `mode="r"`.

The identity endpoint reported the exact checkout, commit, database, data root,
bind and flags without returning credentials. The staging service was safely
restarted during private validation; the live service was not.

## Backup and restore evidence

Production backup: `/home/weidf/shenb/mito-production-backup-20260731T181700Z`.
The PG16 custom archive is 135,655 bytes, has 329 TOC entries and 32 TABLE DATA
entries, and passed archive listing/validation and non-zero checks. Six valuable
data files total 9,696,577,833 bytes and were copied with exact hashes.
EfficientSAM embeddings (17,287 generated files, about 36.45 GB) and 8,596
zero-byte lock files were deliberately excluded as reproducible runtime data.
Protected `.env`, source patches/bundle, service identity and ingress evidence
are included without exposing values.

The production database was restored into `mito_staging`. The deployed
region-mask migration-number collision was reconciled only after exact
PostgreSQL schema comparison, then normal release migrations completed. All
three staging pyramids validate at mags 1/2/4 (and 8/16 for the 2048² volume).

## Correctness and automated gates

- backend: **1226/1226** in one clean PostgreSQL run, 1126.384 s
- focused recheck of the initially misconfigured flag tests: 14/14
- frontend: TypeScript clean; Vitest **151/151**; production build passed
- Phase 14 Chromium: **5/5**
- restored-data staging Chromium, TIFF/default build: **4/4**, five
  chunk/benchmark/soak cases explicitly skipped by their configuration gates
- Django system check: clean; makemigrations: no changes; migrate plan: empty
- deploy check: only HSTS/SSL redirect/secure-cookie warnings, to be resolved at
  the TLS ingress policy before public cutover
- migration isolation was exercised by the full test database; the restored
  staging database has no pending migrations

The first two full backend attempts inherited staging-on flags through dotenv
and correctly failed 12 disabled-default assertions. They are invalid command
evidence, not passing evidence. Explicit `FEATURE_*=0` prevented dotenv from
overriding the release defaults and produced the single green 1226-test run.

## Real workflow results

- collaboration pages, People, Hard Cases and project list rendered restored data
- anonymous full-task share rendered and its public label PUT returned 405
- TIFF XY/XZ/YZ, Brush, Save, reload, and exact working-label persistence passed
- recovery probe returned 200 and Save used versioned `/autosave/`, never legacy
  whole-slice PUT
- two users saving different tasks both returned 200
- two users saving the same task from one base version returned one 200 and one
  409 `stale_version`; zero silent overwrite
- chunk XY/XZ/YZ, token expiry/refresh, corrupt-chunk fail-closed TIFF fallback,
  and chunk-path Save/reload passed in a separate enabled build
- production and external files were hash-checked after testing: 6/6 production
  data files and 5/5 source/reference files remain byte-identical

## Performance and blockers

Phase 14 harness: 512² warm p95 16.0 ms, 2048² full-frame p95 166.0 ms;
retained heap growth 0.89%. Real restored 2048² sequential scrub was materially
worse on chunks: p50/p95/p99 774.7/3382.1/3513.9 ms, 370 requests and 96.5 MB,
versus TIFF 380.3/473.2/477.7 ms, 22 requests and 26.8 MB. The chunk renderer
must remain false.

A 300-second, two-user navigation sample completed 466 cycles with zero page
errors/5xx, but retained heap grew 48.10% and 39.16%, above the 25% sample gate.
The exact 7200-second harness exists but was not run. Consequently RC1 is a
clean, reproducible staging candidate, **not a cutover-approved or tagged
release**.

Fresh detached source validation passed Django checks, migration graph,
`npm ci`, TypeScript, 151 Vitest tests and production build without development
WIP. Git LFS network smudge failed with EOF, as it did during initial staging
creation; explicitly installing the three already hash-verified local LFS
objects produced a clean checkout. This remains an operational prerequisite
for any offline cutover build.

`npm audit` reports four unresolved findings: one high Vite development-server
finding plus its esbuild dependency, and two moderate React Router findings.
The Vite dev server is not part of the production runtime, but remediation
requires the Vite 8 major upgrade. React Router remediation requires the 7.18
major line. Neither breaking upgrade was introduced after the completed product
and browser gates. They require an explicit dependency-upgrade review before a
final production tag.

Live 18188 remained PID 2935215, started 2026-07-29 13:48:30 EDT, and returned
HTTP 200 after validation. Its source HEAD remains `79ad5d9`. Public ingress,
production DB/data and the retired checkout were untouched.
