# Isolated staging deployment

This directory describes the private release-candidate staging identity. It is
intentionally separate from the live 18188 checkout, database and data root.

Runtime chain:

```text
http://127.0.0.1:18189
  -> systemd unit mito-data-studio-staging-20260731
  -> /home/weidf/shenb/mito-data-studio-staging-20260731
  -> PostgreSQL 16 container on 127.0.0.1:5434 / mito_staging
  -> /home/weidf/shenb/mito-data-studio-staging-20260731/data
```

The service runs as the dedicated `mito-staging` OS user. systemd makes the
checkout and `/home/weidf/shenb/wk_data` read-only to the process, with write
exceptions only for staging `data`, `logs`, and `run`. Thus absolute external
source-image/reference-label paths remain readable but cannot be modified by
the staging process.

The PostgreSQL password and Django key belong only in `.env.postgres` and
`.env`; both are ignored and mode 0600. Build the SPA with the same two Vite
flag values declared in `.env`, because Vite flags are compile-time values.

Generate the two protected files without exposing their values or overwriting
an existing staging identity:

```bash
./ops/staging/generate-secrets.sh
```

Create the dedicated Python 3.11 environment strictly from the hash-locked
release dependencies (including the explicitly pinned CUDA wheel index):

```bash
./ops/staging/install-release-dependencies.sh
```

Start with all flags off and `VITE_FEATURE_CHUNK_RENDERER=false` for v1.0.0
parity. The isolated integrated candidate applies `upgrade.env.example`, runs
`verify_upgrade_readiness --strict`, and builds with `npm run build:upgrade`.
That profile enables the complete Phase 1–14 stack while preserving explicit
per-feature overrides for rollback. Never apply the overlay to the frozen
production v1.0.0 checkout or environment.

With the candidate's protected environment already loaded, run the combined
read-only/build gate (set `MITO_PYTHON` when the candidate virtualenv is not on
`PATH`):

```bash
./ops/staging/verify_upgrade_candidate.sh
```

The active deployment already applied an equivalent region-mask migration as
`volumes.0005_volume_region_mask`, before Phase 11 used the same migration
number. On a restored deployment database, reconcile that exact schema before
the normal release migration (the command refuses missing or mismatched
columns, and never changes application rows):

```bash
cd backend
../venv/bin/python manage.py reconcile_legacy_region_mask_migration
../venv/bin/python manage.py reconcile_legacy_region_mask_migration --apply
../venv/bin/python manage.py migrate --noinput
```

For authenticated browser and multi-user validation, create three staging-only
manager identities. Their random credentials are written under the private
runtime directory in an ignored mode-0600 file and never printed:

```bash
sudo -u mito-staging ./ops/staging/create-test-user.sh
```

Run the real-service Chromium checks without putting credentials on a command
line (source the protected runtime file in the invoking shell):

```bash
set -a
source run/.env.staging-test-user
set +a
cd frontend
npx playwright test --config playwright.staging.config.ts
```
