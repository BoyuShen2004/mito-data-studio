# `backend/processing/` — async job queue

A small, generic queue for work that must never run inside an HTTP request:
ingestion, model inference, task generation, visualization conversion, mesh
generation, publishing. Currently thin — the job types exist
(`core.choices.ProcessingJobType`) but most don't have real execution logic
wired up yet (see Gotchas).

## Model (`models.py`)

**`ProcessingJob`** — `job_type` (inspect/ingest/predict/seed/
generate_tasks/quality_control/convert_visualization/generate_mesh/publish),
`backend` (local/slurm), `status` (queued/submitted/running/succeeded/
failed/cancelled). Optional `SET_NULL` links to `Project`/`Volume`/
`AnnotationTask` — deleting a domain object never destroys job history.
`config`/`input_paths`/`output_paths` are JSON. `is_active`/`is_terminal`
properties read `core.choices.ACTIVE_JOB_STATUSES`/`TERMINAL_JOB_STATUSES`.

## Backend interface (`interfaces.py`)

`ProcessingBackend` ABC: `submit(job)`, `poll(job)`, `cancel(job)`, each
returning a `JobResult` (`status`, `external_job_id`, `output_paths`,
`log_path`, `error_message`, `detail`). `collect_outputs(job)` has a
default no-op implementation. Selected via `registry.py` +
`MITO_PROCESSING_BACKEND` (`local` or `slurm` — SLURM adapter config is the
`MITO_SLURM_*` settings in `config/settings.py`).

## Service layer (`services.py`)

The state-machine everything (API, admin actions, and the dispatcher
management command) shares:

- `create_processing_job(...)` — always starts `queued`; never submits
  inline.
- `claim_next_queued_job()` — atomically claims the oldest queued job
  (`select_for_update(skip_locked=True)` on PostgreSQL; a no-op lock on
  SQLite, where a single dispatcher process is assumed — **don't run
  multiple dispatcher processes against the SQLite dev DB**, they'll race).
- `dispatch_job(job)` / `poll_job(job)` / `cancel_job(job)` — call the
  configured backend, apply the `JobResult` via `_apply_result`, then
  `_maybe_finish` (stamps `finished_at`, fires `on_job_finished` once
  terminal).
- `retry_job(job)` — only on terminal jobs; resets to `queued` and bumps
  `retry_count`.
- `run_dispatch_once(max_new=10, poll_active=True)` — one pass: submit up
  to `max_new` queued jobs, poll all active ones. This is what a
  `manage.py run_processing_dispatcher` loop (or a test) calls.
- `on_job_finished(job)` — **currently a placeholder** (`return None`).
  This is the intended hook for "successful ingest → mark volume ready,"
  "successful generate_tasks → create tasks," etc. — not implemented per
  job_type yet.

## API (`api.py`)

`ProcessingJobViewSet` — read-only list/retrieve for everyone (managers see
all; requesters see only jobs on projects they created), plus manager-only
`POST <id>/retry/` and `POST <id>/cancel/` actions calling the service
functions above.

## Running the dispatcher

`python manage.py run_processing_dispatcher` — a deliberately simple loop
(no Celery/Redis): claim queued jobs, submit via the configured backend,
poll active ones, record results, all through `processing.services`.
`--once` for a single pass, `--interval N` to control loop timing. **Not
started by `dev-launch.sh`** — if a feature depends on jobs actually
executing (not just being queued), this needs to be run separately.

Backends are registered in `registry.py`:
`PROCESSING_BACKENDS = {"local": "processing.adapters.local.
LocalProcessingBackend", "slurm": "processing.adapters.slurm.
SlurmProcessingBackend"}`. `adapters/` wasn't read in depth for this doc —
check there directly for what `local`/`slurm` actually do today; the
service layer above only guarantees the *state machine* is correct, not
that every job type has real execution logic behind it.

## Gotchas

- This app is infrastructure that several job types (`ingest`, `predict`,
  `generate_tasks`, ...) don't yet have real per-type completion behavior
  for — `on_job_finished` is a placeholder. If asked to "wire up ingestion"
  or "run inference as a job," the queue/dispatcher/backend-selection
  machinery already exists; what's likely missing is the adapter's actual
  execution logic and/or the `on_job_finished` hook for that job type.
- `claim_next_queued_job`'s row-locking is a no-op on SQLite (the dev DB) —
  don't run multiple dispatcher processes concurrently against it, they'll
  race and can double-claim a job.
