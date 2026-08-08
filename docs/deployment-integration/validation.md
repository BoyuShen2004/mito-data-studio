# Deployment integration validation

Date: 2026-07-30 (America/New_York)

Starting development HEAD:
`25635aed9354041dd7e0b8b5bb62b457d2e0e773`.

Integration branch: `deployment-integration-2026-07-30`.

## Backend

- Complete clean PostgreSQL suite:
  `python manage.py test --noinput annotation core volumes projects accounts processing`
  — **1222 tests, 1057.120 s, OK, 96 expected skips**.
- The Phase 12 baseline was 1203 tests; the integrated suite adds 19
  discovered tests.
- Tests used a Django-created disposable PostgreSQL test database,
  `MITO_DB_CONN_MAX_AGE=0`, and a temporary `MITO_DATA_ROOT`.
- Phase 10 HTTP/storage soak preserved 120/120 cycles, reported zero data
  mismatches and duplicate operations, and left the external source
  byte-identical.
- Django system check: no issues.
- Django deploy check: the same six development-configuration warnings as the
  Phase 12 baseline, no errors.
- `makemigrations --check --dry-run`: no changes.
- `migrate --plan`: only the two new additive region-mask migrations.
- A fresh test database applied the complete migration graph before the 1222
  tests. Existing legacy-row tests, source-ownership tests, migration-safe
  nullable region fields and Phase 11/12 compatibility tests passed.

## Frontend

- TypeScript `tsc --noEmit`: passed.
- Vitest: **7 files, 83 tests, all passed**.
- Mounted autosave/editor and public task-share component tests passed.
- Production Vite build: passed in 2.78 s.
- The existing bundle-size warning remains (main JS about 854 kB before gzip);
  it is not a correctness failure.
- `git diff --check`: passed.

## AI and large-volume behavior

- Prompt ROI planning/remapping, ROI cache identity, best-IoU candidate
  selection, optional CUDA/CPU fallback, XY crop and multi-object propagation
  are covered by deterministic/mocked tests.
- The validation environment has no Torch/SAM2 runtime, so the configured
  provider correctly fell back to the local implementation. No production
  model or GPU state was modified.
- Large-slice history is bounded to 160 MiB; the 3885×4544 regression allows
  two snapshots instead of the former unbounded 20-entry policy.

## Live and production non-interference

- All 71 deployment source-file hashes still match
  `manifests/current-deployment-source-sha256.txt`.
- Current deployment porcelain-v2 status hash:
  `c78c33ac3fbb2e3977d989a18f0d4dfc730098334e143b2cecc58eb44610bcdb`
  (identical to the audit baseline).
- Retired deployment status hash:
  `671eb3addd972b2144200d144c1216fa91ded0cb094634c7ffbf5597b868675a`
  (identical to the audit baseline).
- Production data metadata is unchanged: 17,293 files,
  46,150,386,345 bytes, fingerprint
  `5e0cb6c48ebf696060135942060ced7351aefde21e4cfae377627f2a90ce5731`.
- Port 18188 returns HTTP 200 with the same 400-byte landing response.
- Gunicorn master PID `2935215` still has start time
  `Wed Jul 29 13:48:30 2026`.
- No signal, reload, restart, migration, production-data write, push or deploy
  was issued.

Generated `frontend/dist`, Python caches and the local SQLite placeholder were
excluded. They are ignored build/runtime artifacts and are not integration
inputs.
