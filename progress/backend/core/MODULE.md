# `backend/core/` — cross-cutting concerns

No models of its own (aside from what lives in `choices.py`). Everything
here is shared infrastructure that every other app imports: the enum
vocabulary, the lifecycle rollup, permission classes, file storage, the
Manager Admin site, and dependency-light utilities.

## Files

| File | Purpose |
|---|---|
| `choices.py` | **Every** `TextChoices`/`IntegerChoices` enum in the project: `UserRole`, `AnnotationType`, `WorkflowType`, `ProjectStatus`, `LabelType`, `FileFormat`, `VolumeStatus`, `TaskType`, `TaskStatus`, `PriorityLevel`, `DifficultyLevel`, `QCStatus`, `ReviewDecision`, `ProcessingBackend`, `ProcessingJobStatus`, `ProcessingJobType`. Also a couple of derived maps (`LABEL_TYPE_TO_TASK_TYPE`, `ANNOTATION_TYPE_TO_WORKFLOW`, `ACTIVE_TASK_STATUSES`, `ACTIVE_JOB_STATUSES`/`TERMINAL_JOB_STATUSES`). |
| `lifecycle.py` | The New/To-Proofread/Done rollup — see below. |
| `permissions.py` | DRF `BasePermission` classes: `IsManager`, `IsAnnotator`, `IsRequester`, `CanRegisterData` (manager or requester). Thin wrappers around `accounts.roles` predicates. |
| `storage.py` | `MitoDataStorage` — a `FileSystemStorage` whose `location` is read from `settings.MITO_DATA_ROOT` **dynamically on every access** (not cached at import time), so `@override_settings(MITO_DATA_ROOT=...)` works in tests. `get_mito_storage()` is the callable passed to every `FileField(storage=...)` — a callable, not the instance directly, so migrations serialize a stable reference instead of a machine-specific absolute path. |
| `labels.py` | Internal value → user-facing display label (`role_label`, `term_label`). E.g. `requester`/`client` display as **"Institution"** everywhere in the UI. Has a frontend mirror: `frontend/src/labels.ts` — keep them in sync if you add a role/term. |
| `admin_site.py` | `ManagerAdminSite` — the Django Admin instance managers actually use day to day (`core.admin_apps.ManagerAdminConfig` replaces the default admin app in `INSTALLED_APPS`). Gated to managers/superusers (`has_permission`). Its index page is augmented with dashboard metrics (`_dashboard_metrics`) and lifecycle counts (`_lifecycle_metrics`), each linking to a filtered changelist. |
| `admin_common.py` | `changelist_url(Model, **filters)` — builds a Django Admin changelist URL with query-string filters, used by the dashboard metric links above. |
| `admin_apps.py` | The `AppConfig` that swaps in `ManagerAdminSite` as `django.contrib.admin`'s site. |
| `utils.py` | Format-agnostic volume inspection: `read_tiff_shape_fast` (headers only, no full load), `inspect_volume_shape`, `inspect_volume_voxel_size`, `array_shape_to_xyz`. Both `inspect_*` are cached by (path, mtime). |
| `dev_api.py` | `DevResetView` — dev-only (`if settings.DEBUG`, wired in `config/urls.py` only then), wipes and reseeds demo data. Backs the "Reset dev data" button on the login page. |
| `dev_data.py` | The actual seed-data builder `DevResetView` calls. |

## Voxel size: one source, one unit (`utils.py`)

`inspect_volume_voxel_size` returns **µm** and takes all three axes from a
single source, never a mix:

1. **OME-XML** `PhysicalSize{Z,Y,X}` + their per-axis units, when present.
2. Otherwise ImageJ metadata (`spacing` + its `unit`) plus the TIFF
   `XResolution`/`YResolution` tags scaled by `ResolutionUnit`.
   `ResolutionUnit = none` records an aspect ratio, not a physical size, and
   is ignored.

Why the rule exists: the EM exports in this project carry `PhysicalSize*` in
**nm** *and* a resolution rational that decodes to ~10^6 µm. Filling axes
independently — OME for z, tags for x/y — produced a "voxel" 30,000x wider
than deep, which the 3D view faithfully drew as a flat sheet. Consumers stay
defensive anyway: `annotation.services._render_voxel_size` rejects a triple
whose anisotropy exceeds 50:1 and renders isotropic instead.

Both inspectors are cached by (path, mtime), so re-reading the same headers
across a page of volumes (or every 3D request) costs nothing.

## The lifecycle rollup (`lifecycle.py`)

The product's dashboards don't show the raw, granular statuses
(`TaskStatus`, `VolumeStatus`, `ProjectStatus` — a dozen-plus values across
three models) — they show three buckets: **New → To Proofread → Done**.
Every place that needs this classification (Manager Admin dashboard, React
dashboards via `GET /api/projects/lifecycle-counts/`, the `?lifecycle=`
query param on project list/filter endpoints) goes through this one module
instead of duplicating `if status in (...)` logic.

Mapping (task is the granular anchor — a task is never "New," it either
exists as active work or is done):

```
TaskStatus.UNASSIGNED / ASSIGNED / IN_PROGRESS / SUBMITTED /
  REVISION_REQUESTED / REJECTED         → To Proofread
TaskStatus.APPROVED                     → Done

VolumeStatus.REGISTERED                 → New
VolumeStatus.SPLIT / IN_ANNOTATION      → To Proofread
VolumeStatus.COMPLETED                  → Done
```

`classify_project(project)` rolls a project up from its tasks:
1. `ProjectStatus.COMPLETED`/`DELIVERED` → forced Done, regardless of tasks.
2. Not yet `manager_reviewed`, or zero tasks → New.
3. All tasks approved → Done.
4. Otherwise → To Proofread.

`filter_projects_by_lifecycle(queryset, lifecycle)` evaluates the queryset
in Python (not SQL) — explicitly scoped to "modest project counts," not
million-row tables. If this project ever needs to scale past that, this is
the function to revisit first.

## Permission pattern used everywhere

`core.permissions` classes gate **DRF views at the endpoint level**
("can this role hit this endpoint at all"). Almost every view *also* does a
second, per-object check inline (e.g. `VolumeDetailView.get_object` checking
`volume.project.created_by_id == request.user.id` for a requester) — the
`core.permissions` classes are necessary but not sufficient; read the
specific app's `MODULE.md` (`volumes/`, `annotation/`) for the per-object
rules layered on top.

## Dev-data management commands (`core/management/commands/`)

- `seed_dev` — creates the standard dev accounts (one manager + four
  annotators, password `demo12345`). Registers **no** data — that's done
  manually through the app. Safe to run repeatedly; `--fresh` wipes first.
- `clear_dev_data` — deletes all projects/volumes/tasks/submissions/
  reviews/institutions plus non-superuser accounts, **and everything under
  `MITO_DATA_ROOT`** (`core.dev_data._clear_data_root` — the whole tree's
  contents, not a per-`FileField` loop; a per-field loop alone missed the
  in-app editor's working label copies, written directly by path). Guarded against
  running outside `DEBUG` unless `--force`. `--keep-users` to preserve
  accounts (file wipe happens either way). Also calls
  `annotation.visualization.slice_io.clear_caches()` after the file wipe
  (found while verifying the Cellable-parity work) — without it, the
  Django process's *writable* label-memmap cache (keyed only by path, not
  mtime) keeps serving a stale, now-deleted file's handle to any request for
  the same working-copy path, which SQLite rowid reuse can hand to a
  brand-new volume after a reset. Same reasoning `track_task_fork`/
  `_save_label_volume` already had for clearing caches after a full label
  rewrite — a full data reset is at least as disruptive to what's on disk.
- `reset_dev` — `clear_dev_data` + `migrate` + `seed_dev` in one shot
  (`--no-migrate` to skip migrations). This is what the login page's "Reset
  dev data" button calls via `core.dev_api.DevResetView`.
- `dev_status` — prints current dev-data counts, no changes.

Other apps have their own one-off commands worth knowing about:
`annotation.assign_tasks` (rule-based bulk assignment),
`projects.progress_report` (print progress/workload for one
project), `processing.run_processing_dispatcher` (see
[`../processing/MODULE.md`](../processing/MODULE.md)).

## Gotchas

- `MitoDataStorage.location` resolves `settings.MITO_DATA_ROOT` on *every
  access*, not once at import — if you ever see storage behaving
  inconsistently in a test, check whether `@override_settings` is applied
  around the actual file operation, not just around setup.
- `core.labels` and `frontend/src/labels.ts` are two independent files that
  must be kept in sync by hand — there's no shared source of truth or
  codegen between them.
