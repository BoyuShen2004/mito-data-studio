# `backend/projects/` — Project + Dataset

The top of the data hierarchy: `Project` → `Dataset` → (volumes, owned by
the `volumes` app). Owns project CRUD, the manager-review gate, and
project/dataset-level progress + delete-with-dependents logic.

## Models (`models.py`)

**`Project`** — an annotation project.
- `title`, `description`, `metadata` (JSON — organism, tissue, cell type,
  imaging modality, instrument, conditions, source, publication, etc.;
  intentionally unstructured since biomedical metadata varies a lot).
- `annotation_target` (default `"mitochondria"` — the app was built for
  mito EM but the field is free text).
- `annotation_type` (`semantic_segmentation`/`instance_segmentation`/
  `proofreading`) and `workflow_type` (`annotation`/`proofreading`/
  `segmentation` — the higher-level pipeline choice; see
  `core.choices.WorkflowType` docstring for how these relate: same models
  and services, different configuration/branching, not separate pipelines).
- `status` (`ProjectStatus`: draft/active/in_annotation/in_review/
  completed/delivered/cancelled) — mostly informational; the *lifecycle*
  bucket (New/To Proofread/Done, see `core/MODULE.md`) is derived
  separately and is what dashboards actually show.
- `manager_reviewed` / `reviewed_by` / `reviewed_at` — **the gate**. A
  volume cannot be split into tasks until its project is reviewed
  (enforced by the Assign flow before it creates tasks). Manager-created projects are
  reviewed on creation; requester-created ones start unreviewed.
- `dataset` (a `CharField`) — legacy single-dataset name kept for backwards
  compatibility with old rows; **the real datasets are the `datasets`
  reverse relation**, this field just mirrors the first one.

**`Dataset`** — one registration batch under a project ("this dataset came
from this HPC directory"). Holds `image_directory`/`mask_directory`
(recorded so the registration is reproducible/understandable later) and its
own `metadata` JSON (can differ per-dataset even within one project — e.g.
one project might combine a CellMap dataset and a MitoEM dataset with
different provenance). Unique on `(project, name)`.

## Services (`services.py`)

- `create_project(...)` — the only way to create a `Project`; sets
  `manager_reviewed` based on the `reviewed` kwarg (callers pass
  `is_manager(request.user)`).
- `resolve_workflow_type(annotation_type, workflow_type)` — derives
  `workflow_type` from the older `annotation_type` field when only that's
  given (`core.choices.ANNOTATION_TYPE_TO_WORKFLOW`).
- `mark_project_reviewed(project, reviewer, reviewed=True)` — the review
  gate toggle; also settable to `False` to un-review.
- `calculate_project_progress(project)` — task-status counts + a completion
  percentage, backing `GET /api/projects/<id>/summary/`.
- `get_or_create_dataset(...)` / `update_dataset(...)` — dataset CRUD used
  by both the registration flow (`volumes.services.register_dataset`) and
  direct dataset editing.
- **Delete-with-dependents pattern**, shared by projects, datasets, and
  volumes (the volume half lives in `volumes.services` but reuses this):
  - `describe_project_dependents` / `describe_dataset_dependents` /
    `describe_volume_dependents` — what a delete would take with it
    (volume/task/submission/review counts), so the UI can show an accurate
    warning **before** the user confirms.
  - `delete_project` / `delete_dataset` / `delete_volume` — actually delete,
    raising `DeleteBlocked` (carries `.counts`) unless `force=True` **or**
    there's no annotation work to lose. `_guard()` is the shared "refuse
    unless forced" check. This is deliberately not silent — see
    `core/permissions.py`/the API layer for how `DeleteBlocked` becomes a
    409 with the counts in the body.

## API (`api.py`)

- `ProjectViewSet` (full CRUD + custom actions):
  - Visibility: managers see all projects; requesters see only
    `created_by=self.request.user`.
  - `?lifecycle=new|to_proofread|done` filters via
    `core.lifecycle.filter_projects_by_lifecycle`.
  - `GET lifecycle-counts/` — bucket counts over the caller's visible
    projects (bypasses any `?lifecycle=` filter so every bucket is counted).
  - `GET <id>/summary/` — progress for everyone; `workload` (per-annotator
    task counts, from `annotation.services.calculate_annotator_workload`)
    added only for managers.
  - `GET <id>/dependents/` — pre-delete warning data.
  - `DELETE` — force via `?force=1` or body `{"force": true}`; 409 +
    `{"detail", "counts"}` if blocked.
  - `POST <id>/review/` — manager-only, body `{"reviewed": true|false}`.
- `DatasetViewSet` — same shape, scoped to `?project=<id>`; ownership
  checked both on the dataset's current project **and** any project it's
  being moved to (`perform_update`).

## How this connects to the rest

- `volumes.api`/`volumes.services` reach into `projects.services` for the
  dependents/delete helpers rather than duplicating them.
- `annotation.services.can_view_task`/`can_edit_task` check
  `task.project.created_by_id` for requester visibility — projects are the
  ownership anchor for the whole tree below them.
- `core.admin_site` dashboard metrics query `Project` directly for the
  "awaiting approval"/"approved projects" counts.
