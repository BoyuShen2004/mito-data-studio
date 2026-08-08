# `backend/config/` — Django settings, URL routing

The project's Django settings module (`config.settings`) and root URLconf
(`config.urls`). No business logic — this is wiring.

## `settings.py`

- **Apps**: `core.admin_apps.ManagerAdminConfig` replaces the default admin
  app (see `core/MODULE.md`); local apps are `core`, `accounts`, `projects`,
  `volumes`, `annotation`, `processing`.
- **Auth**: DRF `TokenAuthentication` + `SessionAuthentication`, default
  permission `IsAuthenticated` (endpoints opt out with `AllowAny` where
  needed — login/register/etc.). No pagination
  (`DEFAULT_PAGINATION_CLASS: None`).
- **CORS**: `CORS_ALLOWED_ORIGINS` defaults to the Vite dev server
  (`localhost:5173`), `CORS_ALLOW_CREDENTIALS = True`.
- **`ALLOWED_HOSTS`** (`DJANGO_ALLOWED_HOSTS` env var, default
  `"localhost,127.0.0.1"`) — Django rejects any request whose `Host` header
  isn't on this list (400 `DisallowedHost`), *before* any view code runs.
  `dev-launch.sh` overrides this to `*` for its own launch (not changed
  here in the committed default) because a remote-forwarding proxy — VS
  Code/Cursor Remote-SSH port forwarding, an SSH jump-host tunnel, a
  machine's real network IP — can present a `Host` header that's neither
  `localhost` nor `127.0.0.1`, and the rejection looks indistinguishable
  from "nothing is listening" from the browser. Vite has the identical problem via `server.allowedHosts`,
  fixed the same way in `frontend/vite.config.ts`.
- **`MITO_DATA_ROOT`** — the filesystem root for all image/label/submission
  storage. Resolved from `MITO_DATA_ROOT` env var if set (relative values
  resolve against the **repo root**, the parent of `backend/`, not the CWD
  — so `./data` means the same thing regardless of where you run
  `manage.py` from); defaults to `backend/mito_data_root/`.
- **Provider settings** — `MITO_QC_PROVIDER`, `MITO_PROOFREADING_PROVIDER`
  (default `inapp`), `MITO_VISUALIZATION_PROVIDER` (default `inapp`),
  `MITO_PUBLISHING_PROVIDER`, `MITO_TRACKING_PROVIDER` (default `local`) +
  `MITO_SAM2_ROOT`/`MITO_SAM2_CHECKPOINT`/`MITO_SAM2_CONFIG` (only read on
  a GPU node), `MITO_PROCESSING_BACKEND` (default `local`) +
  `MITO_SLURM_*` (partition/account/sbatch/squeue/sacct/scancel binaries).
  See `PROJECT.md`'s provider table for the full adapter list per setting.
- **`.env`**: loaded via `python-dotenv` from the repo root if present.
  `.env.example` documents every variable — **note it defaults several
  providers to `placeholder`** where `settings.py`'s Python-level default
  is actually a real adapter (e.g. `MITO_PROOFREADING_PROVIDER` defaults to
  `inapp` in `settings.py` but `placeholder` in `.env.example`) — always
  check the actual running `.env` (or lack thereof) rather than assuming
  `.env.example`'s values are what's active; `settings.py`'s `os.getenv(...,
  default)` is authoritative when a key is absent from `.env`.
- **Database**: SQLite, `backend/db.sqlite3`. No Postgres config exists in
  this repo — `processing.services.claim_next_queued_job`'s row-locking
  comment about "multiple dispatcher processes" assumes Postgres in
  production; on this SQLite dev setup, locking is a no-op.

## `urls.py`

- `""` → `config.views.index`, a friendly landing page (the real UI is the
  Vite dev server on `:5173`).
- `admin/` → the Manager Admin (see `core/admin_site.py`).
- `api/auth/*`, `api/annotators/` → `accounts.api`.
- `api/register-data/`, `api/hpc/scan/`, `api/volumes/*`,
  `api/projects/<id>/volumes/` → `volumes.api`.
- `api/projects/<id>/tasks/`, `api/projects/<id>/assign*`, `api/tasks/*`,
  `api/my-tasks/`, `api/my-completed-tasks/`, `api/submissions/*` →
  `annotation.api` (task/submission/review endpoints).
- `api/volumes/<id>/meta|slice|label-slice/`,
  `api/tasks/<id>/track|label-state|label-ids/` → also `annotation.api`
  (the slice-streaming + label-editor surface — see
  `backend/annotation/MODULE.md`).
- `api/` (router) → `ProjectViewSet`, `DatasetViewSet`,
  `ProcessingJobViewSet` (DRF `DefaultRouter`, standard CRUD + custom
  `@action`s).
- `api/dev/reset/` → `core.dev_api.DevResetView` — **only added to
  urlpatterns when `settings.DEBUG` is true**; does not exist at all in a
  non-debug deployment, not just permission-gated.
- Media files (`MEDIA_URL`/`MEDIA_ROOT`) served directly by Django only
  when `DEBUG` — matches the `api/dev/reset/` pattern of debug-only routes.

## Gotchas

- If an endpoint 404s that you're sure exists in `annotation/api.py` or
  `volumes/api.py`, check `urls.py` — imports are grouped by *source file*
  at the top, but paths are grouped by *resource* in the `urlpatterns` list
  below, so a given app's endpoints aren't contiguous in the file.
- `.env.example` is not a reliable guide to what a given deployment's
  providers actually are — read the real `.env` (or the `settings.py`
  Python defaults if no `.env` override exists).
