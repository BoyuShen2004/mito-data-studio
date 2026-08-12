# Architecture

## The big picture

Mito Data Studio has **one backend** and **two front doors**:

```
                 ┌──────────────────────────────┐
 Requesters ───▶ │  React SPA (Vite, :5173)      │ ─┐
 Annotators ───▶ │  frontend/src/*              │  │  token-auth JSON
                 └──────────────────────────────┘  │  over /api/*
                                                    ▼
                 ┌──────────────────────────────────────────────┐
 Managers ─────▶ │  Django + DRF backend (:8000)                 │
 Superusers ──▶  │    api.py  →  serializers.py  →  services.py  │ ─▶ models ─▶ DB
   (Manager      │    admin.py (Manager Admin) ─────┘            │
    Admin)       └──────────────────────────────────────────────┘
```

- **React SPA** (`frontend/`) serves **requesters** and **annotators**. It talks
  to the backend only through the REST API.
- **Manager Admin** (Django Admin at `/admin/`) is the **managers'** operational
  interface. See [admin.md](admin.md).
- Both front doors call the **same models and the same service layer**, so
  behaviour stays consistent no matter who triggers it.

## The golden rule: logic lives in `services.py`

Every app keeps its business logic in `<app>/services.py` as plain, deterministic
functions. Everything else is thin:

| Layer | File | Responsibility |
| --- | --- | --- |
| Service | `<app>/services.py` | **All business logic.** Takes plain args, returns models/dicts. |
| REST view | `<app>/api.py` | Auth/permission check, parse request, call a service, return JSON. |
| Serializer | `<app>/serializers.py` | Validate input / shape output. |
| Admin | `<app>/admin.py` | Manager screens + bulk actions that call services. |
| CLI | `<app>/management/commands/*` | Command-line wrappers that call services. |

**Practical consequence:** to change *what the system does*, edit the service.
To change *how it's exposed*, edit the view, admin, serializer, or page. This is
why the same operation (e.g. assignment) behaves identically from the SPA, the
admin, and a management command.

## Backend apps

| App | Owns | Notable files |
| --- | --- | --- |
| `accounts` | Users, roles, institutions, annotator capacity, auth/registration/login | `models.py`, `roles.py`, `serializers.py`, `api.py`, `signals.py` |
| `projects` | `Project`, approval gate, progress | `models.py`, `services.py`, `api.py`, `admin.py` |
| `volumes` | `Volume`, HPC data registration, scanning, image/mask pairing, splitting | `services.py`, `api.py`, `models.py` |
| `annotation` | `AnnotationTask`, `AnnotationSubmission`, `ReviewRecord`, assignment, QC, review | `services.py`, `api.py`, `admin.py`, `models.py` |
| `core` | Shared enums, permissions, storage, utils, dev-data commands, **Manager Admin site** | `choices.py`, `permissions.py`, `storage.py`, `admin_site.py`, `admin_common.py` |

## Data model

```
Institution ─┬─< Project ─┬─< Volume ─┬─< AnnotationTask ─< AnnotationSubmission ─< ReviewRecord
             │            │           │        │                    │                    │
             └─< UserProfile          └────────┘ (task also FKs Project)                 │
User ─1:1─ UserProfile (role, institution)      assigned_to → User        annotator → User, reviewer → User
User ─1:1─ AnnotatorProfile (is_active_annotator, max_active_tasks, quality_score)
```

- A **dataset** is a `Dataset` under a `Project`; a **volume** is a `Volume`
  (one registered image/label pair). A `Volume` holds **references** to HPC files
  (`image_path` / `label_path`), never the pixel data.
- A `Project` starts **unreviewed** when a requester registers it; a manager must
  approve it (`manager_reviewed`) before its volumes can be split or assigned.
- An `AnnotationTask` covers a z/y/x range of a volume; the default auto-assign
  path makes **one whole-volume task per volume**.
- Enums (roles, statuses, types) live in `core/choices.py` and are mirrored on
  the frontend in `frontend/src/types/index.ts`.

## Request lifecycle (SPA example: submitting a label)

1. `frontend/src/pages/SubmitTaskPage.tsx` calls `submitTask()` in
   `frontend/src/api/submissions.ts`.
2. `frontend/src/api/client.ts` sends the request with the auth token to
   `POST /api/tasks/<id>/submit/`.
3. `config/urls.py` routes to `SubmitTaskView` in `annotation/api.py`.
4. The view checks the permission, validates with `SubmitTaskSerializer`, and
   calls `submit_annotation()` in `annotation/services.py`.
5. The service creates the `AnnotationSubmission`, runs `run_basic_qc()`, and
   flips the task status — the same service the Manager Admin would use.

## Lifecycle, terminology, and workflow types

- **New / To Proofread / Done** is one centralised mapping in
  `core/lifecycle.py` over the *existing* statuses (project review gate + task
  rollup; volume status). Everything — the project serializer's `lifecycle`
  field, the `?lifecycle=` list filter and `/api/projects/lifecycle-counts/`,
  the Admin `LifecycleFilter` and dashboard, and the React tabs — reads from
  this module. There are **no new lifecycle tables**.
- **Terminology.** The internal role value stays `requester`; the UI shows
  **Institution**. The internal→display mapping is centralised in
  `core/labels.py` (backend) and `frontend/src/labels.ts` (frontend). No
  database values or migrations were renamed.
- **Workflow type** (`annotation` / `proofreading` / `segmentation`) is a field
  on `Project` (`WorkflowType`), defaulted from the older `annotation_type`.
  The three workflows share one registration → volume → task → submission →
  review → result pipeline; they differ only by configuration and service-layer
  branching, not duplicated code.

## Modular providers (replaceable integrations)

Replaceable integrations sit behind small interfaces — an ABC in
`interfaces.py`, a settings-driven `registry.py`, and `adapters/`. **Domain
services call the registry; admin/API/React never import an adapter.** Each is
chosen by a `settings.MITO_*` value.

| Provider | Package | Default | Setting |
| --- | --- | --- | --- |
| Quality control | `annotation/quality_control/` | `basic` (original file checks) | `MITO_QC_PROVIDER` |
| Proofreading | `annotation/proofreading/` | `inapp` (in-app editor) | `MITO_PROOFREADING_PROVIDER` |
| Visualization | `annotation/visualization/` | `inapp` (slice viewer) | `MITO_VISUALIZATION_PROVIDER` |
| SAM2 tracking | `annotation/tracking/` | `local` (CPU stand-in) | `MITO_TRACKING_PROVIDER` |
| Publishing | `annotation/publishing/` | `placeholder` | `MITO_PUBLISHING_PROVIDER` |
| Processing backend | `processing/adapters/` | `local` (mock) | `MITO_PROCESSING_BACKEND` |

The proofreading provider distinguishes **view** from **edit**: a read-only
viewer reports `editable=False`, so the UI never implies edits are saved.
`run_basic_qc` is unchanged in behaviour — it delegates to the QC provider.

### Visualization, in-app annotation, and fork-aware tracking

- **Slice IO** (`annotation/visualization/slice_io.py`) opens volumes as
  memory-maps and streams one windowed PNG slice at a time through bounded LRU
  caches (Cellable's `sliceCache`/`MAX_SLICE_PIXMAP_CACHE` pattern, on the
  server). The `inapp` visualization provider points the SPA's `SliceViewer`
  (LRU object-URL cache + neighbour prefetch) at these endpoints.
- **Role gating** lives in `annotation/services.py` (`can_view_task`,
  `can_edit_task`, `can_view_volume`): requesters view; managers and the assigned
  annotator edit. The launch info is downgraded server-side, and mutation
  endpoints reject non-editors — the UI never decides access alone.
- **SAM2 tracking** (`annotation/tracking/`, ported from `MTS`) seeds each fork
  branch as its own temporary track id, groups them, propagates (GPU `sam2` via a
  processing job, or CPU `local`), then **auto-merges the group into one final
  mitochondria instance** (`run_branch_tracking`). Branch/final ids + group
  membership persist in `volume.metadata['tracking_groups']`.

## ProcessingJob and the dispatcher

Heavy/async work (ingest, predict, generate tasks, convert, mesh, publish…) is a
`processing.ProcessingJob` row, never run inside an HTTP request. The API/Admin
only **create/retry/cancel** jobs; the dispatcher executes them:

```
create_processing_job()  →  queued
run_processing_dispatcher →  claim (row-locked) → backend.submit() → poll → terminal → callback
```

Backends: `LocalProcessingBackend` (dev/tests, simulates success + writes a
marker output) and `SlurmProcessingBackend` (sbatch/squeue/sacct/scancel; all
cluster values from `MITO_SLURM_*` env). No Celery/Redis. On SQLite the row lock
is a no-op (single dispatcher assumed); on PostgreSQL it uses
`select_for_update(skip_locked=True)`.

## Storage & configuration

- Files live under `MITO_DATA_ROOT` (see `.env`); the DB stores only relative
  paths. Path resolution is in `core/storage.py` and `volumes/services.py`.
- Runtime settings are in `config/settings.py`, driven by `.env`
  (see [development.md](development.md)).
