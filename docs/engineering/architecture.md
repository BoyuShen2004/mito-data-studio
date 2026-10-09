# System architecture

Start with the [code map](code-map.md) and [feature walkthrough](feature-walkthrough.md) for an implementation reading path.

## Component overview

```text
Browser (React + TypeScript + Canvas/Three.js)
        |
        | HTTPS / JSON / PNG or chunk payloads
        v
Django REST API (gunicorn)
        |                  \
        |                   \ queued heavy jobs
        v                    v
PostgreSQL/SQLite       Processing dispatcher
metadata/workflow       local processing backend or SLURM processing backend
        |
        v
MITO_DATA_ROOT / uploaded storage
source volumes, working labels, snapshots, embeddings, pyramids, logs
```

The production-shaped Docker deployment serves the compiled single-page
application with WhiteNoise from the Django/gunicorn process and runs
PostgreSQL as a separate service. The full development Docker stack uses the
same basic topology. A host-development option runs Django and Vite separately.

## Backend organization

| Django app | Responsibility |
| --- | --- |
| `accounts` | Authentication, profiles, roles, institutions, teams, people, audit events |
| `projects` | Projects, datasets, memberships, lifecycle, and public shares |
| `volumes` | Volume registration, metadata, pairing, ROI coverage, pyramids, chunk serving |
| `annotation` | Tasks, editing plans, labels, Track, AI prompts, submissions, reviews, hard cases, timing |
| `processing` | Durable jobs, dispatcher, local execution, and SLURM integration |
| `core` | Shared choices, storage ownership, security checks, maintenance, observability, reset safety |

Business rules live in service modules rather than views. Django REST Framework
views authenticate, authorize, decode input, call services, and serialize
results. Database migrations are additive and form part of the release record.

## Frontend organization

The frontend is a React 18/TypeScript single-page application using React
Router. One personal home at `/` serves every role, its tabs decided by role;
shared project/volume/task pages; a full-window viewer/editor; hard-case pages;
profile/people pages; and unauthenticated read-only shares.

Tasks, hard cases, and projects are rendered by a single list component
(`components/WorkList.tsx`) whose filter state lives in the query string, and a
task's history is derived at render time by `features/worklist/timeline.ts` from
fields the task serializer already returns. There is no task-conversation event
table; selected actions separately persist audit events in `accounts/models.py`.

The annotation canvas combines browser-side pending label slices with
server-planned operations. This allows previews and compound Undo/Redo without
persisting every intermediate model or deterministic-tool result.

## Frontend refresh and draft ownership

The page, editable setup, and result display have separate lifetimes. Preserve
these boundaries when extending project and volume workflows:

| Owner | State and refresh contract |
| --- | --- |
| `hooks/useAsync.ts` | Keeps the last successful payload while reloading; ignores replies from an obsolete effect. It does not abort the underlying HTTP request or provide a shared cache. |
| `pages/ProjectDetailPage.tsx` | Retains a loaded, matching project during refresh so team saves do not unmount the assignment editor. A different project ID must not display the prior project's data. |
| `pages/VolumeDetailPage.tsx` | Retains a loaded, matching volume and metadata draft during refresh. Automatic pyramid polling waits three seconds after the previous refresh settles and stops when neither layer is building. Task-list dependencies use project/volume IDs; status-only updates do not reload every project task. Explicit metadata saves refresh both volume and task data. |
| `features/extensions/ProjectExtensions.tsx` and `registry.ts` | Own catalog selection through the URL; mount only the selected enabled extension. Typed context shares project and volume state. A render/load error boundary leaves core tabs usable. See [extension guidelines](extensions.md). |
| `components/ProjectMeasurements.tsx` | Setup is keyed by volume ID. Unrelated list refreshes preserve pending spacing and the selected source. Detection reruns when the ID, registered spacing, or label availability changes; switching volume starts separate setup. |
| `components/MitoMeasurements.tsx` | Owns the selected source. Its result child is keyed by volume ID, source and voxel spacing; input changes clear the cached result and refetch freshness without resetting the source. Only an explicit run action queues computation. |

Refresh errors remain visible alongside retained state. Cached data is the last
successful response, not a promise that access or metadata is still current;
backend authorization and input checks apply to each action. Retaining a draft
through refresh does not persist it across leaving the page or closing the tab.

Use scalar request dependencies rather than refreshed object identity when the
endpoint inputs have not changed. Keep effect cleanup for timers and late
responses. Regression checks should exercise a delayed response, a failed
refresh, changing entity ID, and explicit saves; include request counts when a
status refresh could accidentally fetch unrelated lists. Do not infer that
keeping a component mounted makes its cached scientific result current.

These changes reduce repeated requests; they do not establish an application
capacity limit. The volume page still retrieves the project task list and
filters it in the browser. Large-project scalability requires payload, query,
latency and memory measurements before changing that API contract. Any list
embedding `AnnotationTaskSerializer` must retain `TASK_SELECT_RELATED` and
`TASK_PREFETCH_RELATED` in `backend/annotation/api.py`. See the
[workflow audit](../research/workflow-simplification-audit.md) for remaining
navigation and state-lifetime issues.

## Core data flow

1. Registration stores a validated source path and inspected metadata.
2. Assignment creates or updates a whole-volume annotation task.
3. The viewer reads source slices or validated pyramid chunks.
4. Editing tools create browser-side pending planes or server-returned plans.
5. Explicit Save, Verify's save flush, and best-effort autosave write
   revision-checked working-label planes and lifecycle metadata.
6. Submit creates an immutable snapshot for one submission channel.
7. Manager approval promotes the chosen snapshot to the official label and
   creates a fresh working state. Working-team withdrawal can instead repoint
   the official label to the saved working TIFF without approval.

## Plan/preview/apply pattern

Whole-volume or model-assisted operations preferentially return planned slices
with before/after run-length encodings. The browser applies them to its pending
buffer. The HTTP plan endpoints do not persist label planes; the browser stages them
for inspection and compound Undo/Redo. Browser-local Flood fill and Interpolate
also stage edits; the latter uses pixel geometry without physical spacing.
The editor can autosave staged planes. In particular unresolved Track previews
are not excluded by the current autosave callback; see the
[audit caveat](../research/documentation-audit.md#ambiguities-and-suspected-implementation-bugs-no-runtime-changes).

SAM2 Track uses a bounded z slab spanning queued ranges. Classes are applied in
request order so earlier classes win protected collisions. The SAM2 adapter is
a process-local singleton protected by a re-entrant lock because its inference
state is mutable. Point, Box, and Boundary prompts use an image predictor built
on the same loaded model, created and used under its own lock.

## Background processing

`backend/processing/models.py` defines a durable database record of job type,
backend, status, optional project/volume/task, config, input/output paths, log,
error, retries and timestamps. Domain links use `SET_NULL`: job history survives
deletion. `run_processing_dispatcher` claims queued jobs and executes/polls them
through `processing/services.py`. PostgreSQL claims use row locks and skip-locked
where supported; SQLite assumes a single dispatcher.

The type choices are `inspect`, `ingest`, `predict`, `seed`, `generate_tasks`,
`quality_control`, `convert_visualization`, `generate_mesh`, `build_pyramid`,
`measure_mito`, and `publish`. These are vocabulary, not complete scientific
pipelines. Current application producers are pyramid requests
(`volumes/pyramid/jobs.py`) and measurements (`annotation/measurement_api.py`).
Measurement API jobs explicitly select `local`. Local pyramid and measurement
jobs use dedicated Python runners directly inside the dispatcher, bypassing the
generic adapter. They are asynchronous relative to HTTP but execute synchronously
inside that dispatcher process. Other types require caller-supplied commands or
otherwise use the generic no-command mock. `on_job_finished` is an empty hook;
it does not ingest artifacts, create tasks or publish scientific results.

### Local processing backend

`processing/adapters/local.py:LocalProcessingBackend` runs synchronously during
`submit`; there is no separate local processing service beyond the dispatcher.

- Without `config.argv` (missing or null), it writes a deterministic `result.json`
  marker and `job.log`, then reports success. This does **not** run inference or
  another scientific algorithm.
- With `config.argv`, it requires a nonempty list of nonempty strings and checks
  the executable **basename** against comma-separated
  `MITO_LOCAL_EXECUTABLE_ALLOWLIST` (empty by default). This is not an absolute
  executable-path trust check. The caller supplies the scientific command and
  inputs; the adapter invokes argv without a shell.
- The working directory is `MITO_SHARED_STORAGE_ROOT/processing_jobs/<job id>`;
  the shared root defaults to `MITO_DATA_ROOT`. Only variables named in
  `MITO_PROCESSING_ENV_ALLOWLIST`, plus host `PATH`, are inherited. Job config
  cannot inject environment variables. `MITO_LOCAL_JOB_TIMEOUT_SECONDS` defaults
  to 86400; timeout, launch error or nonzero exit fails the job. Stdout/stderr
  stream to `job.log`.
- Success inventories files recursively, excluding `result.json` and `job.log`,
  with relative path, byte count and SHA-256 in a schema-1 result manifest.
  This inventories artifacts, not validates their scientific meaning.
- Poll echoes recorded state. Cancel records cancelled and does not kill an
  executing subprocess. There is no dispatcher lease or interrupted-job recovery.
  Retry requeues a terminal job, including succeeded jobs, and does not clean old
  output paths/files. Operators must assess safe reruns.

### SLURM processing backend

`processing/adapters/slurm.py:SlurmProcessingBackend` supplies scheduler glue.
The caller supplies `config.sbatch_script` or legacy `config.command` (passed as
an **sbatch script path**, not executed as an inline command). Otherwise valid
`config.argv` generates a private mode-0700 Bash script using `shlex.join`.
The adapter does not generate scientific code per job type. The local executable
allowlist/environment filter/timeout are not enforced by this adapter.

Submission calls `MITO_SLURM_SBATCH` with configured `MITO_SLURM_PARTITION`,
`MITO_SLURM_ACCOUNT`, a job name and shared-root log path. It parses the job ID
from sbatch stdout. Poll calls `MITO_SLURM_SACCT`, takes the first State row and
maps PENDING/CONFIGURING → submitted, RUNNING/COMPLETING → running, COMPLETED →
succeeded, FAILED/TIMEOUT/NODE_FAIL/OUT_OF_MEMORY/BOOT_FAIL → failed, CANCELLED →
cancelled. Empty/unknown states preserve prior status; sacct failure retains
status and records an error. There is **no squeue fallback** despite the retained
`MITO_SLURM_SQUEUE` setting. Cancel calls `MITO_SLURM_SCANCEL`; errors preserve
prior status.

Application and compute nodes must see the same script/input/output paths and
have the caller's scientific executable/dependencies available. No automatic
artifact scan, copy-back or installation is implemented: inherited
`collect_outputs` merely returns existing paths, and the dispatcher does not
call it. Scheduler polling/cancellation have not been verified against a live
cluster in this documentation audit.

### Interactive SAM2 inference runtime

Point Mask, Box Mask, Boundary and the editor's Track batch-plan requests call
annotation inference services within HTTP requests. They do not create
`ProcessingJob` rows and SAM2 is not a third processing backend. CUDA runtime,
checkpoint loading, process-local locks and feature caches are separate from the
queued path. Queue entries in the Track rail store prompts, not durable scheduler
jobs. A failed/timed-out request has no dispatcher retry or background result
retrieval; users must retry or continue manually.

## Integrity and security boundaries

- Registered images and region masks are read-only inputs.
- Owned working artifacts are restricted to the configured data root.
- Writes use path ownership checks and serialized file-write locks.
- Deleting a project, dataset, or volume removes its generated artifacts after
  the transaction commits, preserving original sources, surviving references,
  symlinks and external paths. Generated approved labels can be removed; generic
  processing-job outputs/history are retained. See [storage](data-and-storage.md#owned-artifacts-and-deletion).
- The SPA stores an API token in localStorage and sends `Authorization: Token`.
  DRF also supports session authentication. Session writes require CSRF; ordinary
  token-authenticated API writes do not generally require CSRF. Destructive reset
  explicitly requires CSRF even for token-authenticated callers. Server-side
  role/project checks and optional TLS/proxy hardening remain essential.
- Database-backed project/dataset/volume and hard-case tokens expose scoped
  read-only views with revocation. Legacy signed task tokens have no per-link
  revocation or age expiry (`annotation/task_sharing.py`).
- Production reset requires authentication, password re-check, an exact
  phrase, a fresh backup marker, maintenance mode, and deployment identity.
- Development reset is separately gated and must never be enabled on valuable
  deployments.

## Versioned implementation stack

The release lock currently specifies Django 5.1.15, Django REST Framework
3.17.1, NumPy 2.4.6, SciPy 1.17.1, scikit-image 0.26.0, PyTorch 2.5.1 with CUDA
12.4 wheels, Zarr 3.1.6, tifffile 2026.3.3, h5py 3.16.0,
nibabel 5.3.2, gunicorn 26.0.0, and PostgreSQL through psycopg 3.3.4. The
frontend declares React 18.3.1, TypeScript 5.5.4, Vite 6.4.3, Three.js 0.170,
Vitest 4.1.10, and Playwright 1.62.1. Use lock files—not this prose—as the
installation authority.

