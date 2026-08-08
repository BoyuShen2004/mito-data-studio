# `backend/annotation/` — tasks, submissions, review, the label editor, tracking

The largest app (~5000 lines). Owns the actual annotation *work* — tasks,
who's assigned, submissions, manager review — **and** the slice-streaming +
in-app label-editing backend, **and** fork-aware SAM2 tracking, **and** the
provider interfaces for proofreading/visualization/tracking/QC/publishing.
If you're looking for where the editor's data actually lives on disk, or why
a request is slow, start in `visualization/slice_io.py` and
`services.py`'s label-id functions.

## Models (`models.py`)

- **`AnnotationTask`** — a z-range of one volume assigned to one annotator.
  `z_start/z_end` (required), `y_start/y_end`/`x_start/x_end` (default to
  the full volume — most tasks are whole-volume-in-y/x, split only in z).
  `task_type` (`core.choices.TaskType`), `status` (`TaskStatus`),
  `priority`/`difficulty` (1–5 each), `deadline`. `frame_label` property →
  `"z {start}–{end}"`.
  Also carries the **submit/review loop state**: `annotation_locked` (the
  single gate on "may the annotator still work on this?" — set when a manager
  approves without allowing further annotation), `submission_count`, and the
  denormalized `last_decision*` fields (decision, at, by, comments) so "what
  did the manager say?" is a one-row read.
- **`AnnotationSubmission`** — a submission for a task, from one of two
  `source`s (`core.choices.SubmissionSource`): `upload` — an uploaded label
  file (`label_file`, via `core.storage.get_mito_storage`); or `inapp` — a
  checkpoint of the volume's in-app-edited working label copy, `label_file`
  null. `qc_status`/`qc_report` (from the QC provider — branches on
  `source`, see `quality_control/adapters/basic.py`), `notes`. **At most one
  row per task survives** — re-submitting deletes the previous one (see
  `_supersede_submissions`).
- **`ReviewRecord`** — a manager's decision (`ReviewDecision`:
  approved/rejected/revision_requested) on a submission, with `comments`.
  Deliberately **outlives the submission it decided**: `submission` is
  `SET_NULL` and `task` is the durable FK, so latest-only submission pruning
  doesn't erase the decision log the People panels count.
- **`HardCase`** (was `HardCaseShare`) — a hard case recorded **against a
  project**. An editor ("Record hard case" beside Save) captures the Active
  `label_id` on a `task`, denormalizes `project`/`volume` so the project and
  inbox lists are one indexed query, and mints an unguessable `token`
  (`secrets.token_urlsafe(32)`). Two audiences on one row:
  - **project members** — the manager(s), the requester, and every annotator
    with a task on the project — open it at `/hard-cases/<id>`, no token;
  - **anyone with the token URL** gets the original view-only, no-account
    viewer, for pasting outside the app.

  `status` (`open`/`resolved`) is the in-project lifecycle: "take down"
  resolves (+ `resolved_by`/`resolved_at`) rather than deleting, so members
  keep read access to settled cases. `revoked` is separate and kills only the
  public token. See the `HardCase*` + `PublicHardCase*` views in `api.py` and
  `progress/history/{02-share-hard-case,05-submit-people-hardcases}.md`.

## Services (`services.py`, 760 lines) — organized by section

### Assignment
- `assign_tasks_rule_based(project=None)` — evenly distributes unassigned
  tasks across active annotators (respecting `max_active_tasks`).
- `preview_assign_project(project)` / `apply_assignment_plan(project,
  entries, annotators_by_id)` — the two-step "preview a plan, let the
  manager edit it, then apply" flow backing `AssignmentPlanEditor` on the
  frontend.
- `assign_task_to_annotator(task, annotator=...)` — single-task manual
  assign.
- `auto_assign_project(project)` / `ensure_volume_tasks(project)` /
  `create_whole_volume_task(volume)` — bulk helpers.

### Submission + review — **staging/approval gate, read this alongside "Label persistence" below**
- `can_submit_task(user, task)` — **the** Submit gate, serialized as
  `can_submit` and enforced by both submit endpoints: edit access **and**
  `not task.annotation_locked`. Deliberately **not** a status list. The
  frontend used to gate on `["assigned", "in_progress",
  "revision_requested"]`, which made "Submit for review" vanish the moment a
  task went to `submitted`; the annotator may submit again after a previous
  submit, a reject, a revision request, or an approve that stayed open.
- `can_annotate_task(user, task)` — the same rule for *painting*. Every
  working-copy mutation endpoint gates on it, so a locked task 403s on the
  API rather than merely hiding buttons.
- `submit_annotation(task, annotator, label_file, notes)` — the **upload**
  path: creates an `AnnotationSubmission` (`source=upload`), supersedes the
  task's previous submission, runs QC (`run_basic_qc`), transitions the task
  to `submitted`. Approving one still does **not** touch
  `Volume.label_path`/`label_file` (that pathway has never merged an upload
  back into the volume's official label; out of scope).
- `submit_inapp_annotation(task, annotator, notes)` — the **in-app** path:
  no file to upload, so this just checks the volume's *working* label copy
  exists (raises `ValueError` if nothing's been painted/tracked yet) and
  creates an `AnnotationSubmission` (`source=inapp`, `label_file=None`).
  Both paths raise on a locked task.
- `_supersede_submissions(task, keep=…)` — **latest submission wins**. Deletes
  every other submission row for the task and unlinks any uploaded
  `label_file` from storage explicitly (a queryset `.delete()` drops rows but
  orphans files — the bug behind
  `progress/history/17-fix-dev-reset-orphaned-files.md`). In-app submissions
  own no file: they point at the volume's shared working copy, which the
  *next* submission still needs.
- `review_submission(submission, reviewer, decision, comments,
  allow_further_annotation=False)` →
  `approve_submission`/`reject_submission`/`request_revision` — each
  records a `ReviewRecord` via `_record_review` (which also denormalizes the
  decision onto the task) and transitions the task status accordingly.
  **`approve_submission` is also the one moment an in-app submission's
  working copy gets promoted** to the volume's official label
  (`_promote_working_label_to_official` → `_repoint_label`) — see below.
  Rejecting or requesting revision never promotes anything.
- `set_task_annotation_lock(task, locked=…)` — a manager reopening a task they
  closed on approve (or closing one they left open), without staging a fake
  review round.

#### Status machine

| From | Event | To | `annotation_locked` |
|---|---|---|---|
| assigned / in_progress / revision_requested / rejected / submitted / approved-but-open | submit | `submitted` | unchanged (`False`) |
| submitted | approve, `allow_further_annotation=False` | `approved` | **`True`** — paint + submit 403 |
| submitted | approve, `allow_further_annotation=True` | `approved` | `False` — another round is allowed |
| submitted | reject | `rejected` | `False` |
| submitted | request revision | `revision_requested` | `False` |
| any | `POST /tasks/<id>/annotation-lock/` | unchanged | as sent |

`rejected` and `revision_requested` stay **distinct statuses** (the verdict is
worth showing on a badge) but neither gates anything — `annotation_locked` is
the single gate, which is what makes "pick one consistently" true here without
throwing away the distinction. `/my-tasks/` therefore lists `rejected`
alongside the active statuses: it is work to redo, not history.

### Role-based view/edit access — **the permission core of this whole app**
```python
can_edit_task(user, task)      # manager, or task.assigned_to == user
can_annotate_task(user, task)  # can_edit_task AND not task.annotation_locked
can_submit_task(user, task)    # same rule — the Submit gate (see above)
is_project_member(user, project)  # manager, the project's requester (created_by),
                                  # OR any annotator with a task on the project
can_view_task(user, task)      # can_edit_task, OR task.assigned_to == user,
                               # OR is_project_member(user, task.project)
can_view_volume(user, volume)  # manager, an annotator with a task on the volume,
                               # OR is_project_member(user, volume.project)
```
`is_project_member` is the project-centric membership rule the whole
collaboration layer rests on: hard-case visibility, People, and task/volume
*viewing* all read it. It deliberately admits an annotator whose own task is a
*different* volume of the same project — a hard case someone flags is for the
team to look at, and teammates read it through the ordinary authed viewer
rather than a second permission system. Editing is unaffected: `can_edit_task`
still requires assignment.
Every slice/label-editor endpoint in `api.py` calls one of these — not a
DRF permission *class* (those gate "can hit this endpoint type at all"),
these are **per-object** checks. If you add a new task/volume-touching
endpoint, use these, don't reinvent the ownership logic.

### Provider bridges
- `get_task_proofreading_info(task, user)` — asks the configured
  proofreading provider for launch info, then **downgrades to view-only
  server-side** if `user` can't *annotate* the task — no edit access, or the
  manager approved-and-locked it (even if the provider itself
  would advertise `mode="edit"`) — a requester can never get an edit URL
  from this function no matter what the provider says.
- `get_task_download_descriptor(task)`, `get_visualization_state(volume_or_task)`
  — thin provider-registry wrappers.

### Label persistence — **read this before touching anything here**

**Working copy vs. official label.** Every in-app edit (paint or SAM2
tracking) writes only to the volume's *working* copy — a deterministic,
always-the-same path computed by `annotation.label_paths.
working_label_rel_path(volume)`: `<project name>/<dataset name or
"no-dataset">/<image stem>_mask.tif` under `MITO_DATA_ROOT`. The mask is
named from the volume's **registered image basename** (a final `.tif`/
`.tiff`/`.h5`/`.hdf5` stripped, inner suffixes like `.ome` kept), *not*
`volume_<id>_labels.tif` — the id only reappears as `_v<id>` on a rare
same-dataset-folder stem collision. Its lifecycle sidecar lives in a
`metadata/` subfolder beside the mask and its SAM embeddings in an
`embeddings/<variant>/` subfolder beside it — everything for one volume in
one place, never a global `data/embeddings/` silo (see
`annotation/label_paths.py`'s module docstring for the full layout, the
external-image rule, and why a folder-name collision between two
identically-named projects is harmless). The pre-`_mask` scheme is migrated
by `manage.py migrate_volume_artifacts` and adopted lazily on first edit
(`services._adopt_legacy_working_copy`), so no already-painted voxels are
lost across the rename.
It **never** touches `volume.label_path`/
`label_file`/`label_type` (the *official*, approved label that every other
viewer sees) — that only changes in one place: `approve_submission`,
promoting an in-app submission's working copy to official (see "Submission
+ review" above). Before approval, the working copy is purely a staging
area — reject/revision-requested never promotes anything, so half-finished
or wrong edits never become "the" mask. This is what backs the user-facing
ask: annotators/managers always edit a copy, never the original, and that
copy only replaces the original once a manager approves it.

Two completely different code paths exist, on purpose, for two different
access patterns to that working copy:

**1. Whole-volume read/write** (`_load_or_init_label`, `_save_label_volume`,
used by `track_task_fork`) — loads the *entire* label array into memory,
mutates it, writes it all back with `tifffile.imwrite`. Fine for SAM2
tracking (rare, user-triggered, and the algorithm needs real array
semantics for its temporary branch-id bookkeeping — see `tracking/`) but
**catastrophic if used per-slice**: measured at 8.75 seconds for one slice
edit on a realistic (1.9GB) label volume. This *was* the code path the in-app editor used, and it's why the editor
was unusably slow before that fix. `_load_or_init_label` seeds from the
volume's *official* label (`label_location`) the first time — i.e. tracking
starts from the last-approved state, not a stale in-progress one.

**2. Per-slice memmap read/write** (`_writable_label`, `get_label_slice_ids`,
`set_label_slice_ids`, `get_label_max_id`) — the hot path, used by the
in-app editor on every slice-open and every paint-stroke commit. Opens the
label file as a **writable memory-map**
(`visualization.slice_io.open_label_volume_writable`) and touches only the
one 2D slice being read/written. ~15ms/stroke instead of ~9s. `get_label_max_id`
(the editor's "next new instance id" bootstrap) reads the **working** copy's
max id, not the official one — the editor needs the id following whatever's
already been painted, even before anything is approved.

`set_label_slice_ids` also takes an `origin: "manual" | "ai"` keyword (the
`PUT /label-ids/` body's `origin` field, defaulting to `"manual"`) and
diffs the slice's previous content against the incoming one to update the
Proposed/Edited/Verified lifecycle sidecar (`cellable_port/label_state.py`)
for every id whose pixels actually changed — never for ids merely present
but untouched, since a commit always resends the whole slice. See that
module's summary above for the exact state rules.

**Neither path ever writes to the original registered path** — the first
edit "forks" a working copy at `working_label_rel_path(volume)`, seeded from
whatever the volume's official label pointed at *before* the first edit (or
zeros if nothing existed). This is the fix for a real data-safety incident — **do not** make either path
write directly to `volume.label_location`. `_repoint_label(volume, rel)`
(promotion only, called from `approve_submission`) is the **only** place
`volume.label_path`/`label_file` change; do not call it from the paint/track
paths again — that was the pre-approval-gate behavior, since replaced by the approval gate.

Max-instance-id tracking (`slice_io.label_max_id`/`bump_label_max_id`) is
similarly cached per-process and updated incrementally — an earlier version
of the per-slice path recomputed this with a full-volume `.max()` scan on
*every* stroke, which was the second (smaller) performance bug fixed in the
same round. May overestimate after erasing the volume's only instance of
the previous max (never rescans down) — harmless, only used to suggest the
next free id.

### Fork-aware tracking persistence
- `track_task_fork(task, seeds_by_z, z_range=None)` — the service-layer
  entry point `api.TaskTrackView` calls. See `tracking/` below for the
  algorithm; this function is the Django-facing glue (loads the label via
  path 1 above, calls `tracking.services.run_branch_tracking`, saves the
  result to the *working* copy only, records `volume.metadata
  ['tracking_groups']` — that metadata write is immediate/unconditional
  audit bookkeeping, not the label pixels themselves, so it's fine that it
  doesn't wait for approval).

### Workload
- `calculate_annotator_workload(project=None)` — per-annotator active/
  submitted/approved/total counts, backing the manager dashboard and
  `ProjectViewSet.summary`'s `workload` field.

## API (`api.py`, 594 lines)

Grouped by what they serve:

**Task/submission/review CRUD** — `ProjectTasksView` (manager-only list),
`TaskDetailView`, `AssignTasksView`/`AssignmentPlanPreviewView`/
`AssignmentPlanApplyView`/`AssignTaskView`, `MyTasksView`/
`MyCompletedTasksView` (annotator's own), `SubmitTaskView` (upload),
`SubmitInappTaskView` (in-app — `POST /api/tasks/<id>/submit-inapp/`), both
gated on `can_submit_task` rather than assignment alone (a manager who
directly edited a task should also be able to submit; a locked task 403s),
`SubmissionListView` (**latest submission per task only** — see
`latest_submission_ids`) / `SubmissionDetailView`, `ReviewSubmissionView`
(takes `allow_further_annotation`), and `TaskAnnotationLockView` (`POST
/api/tasks/<id>/annotation-lock/`, managers only).

**Hard cases** — `HardCaseCreateView` (`POST /api/tasks/<id>/hard-cases/`,
`can_edit_task`; a *locked* task can still have cases recorded, since flagging
one is not annotating it), `HardCaseListView` (the inbox, scoped by
`visible_hard_cases`, `?project=`/`?volume=`/`?status=`), `HardCaseDetailView`,
`HardCaseStatusView` (take down / reopen), `HardCaseRevokeView` (public token
only). Permission matrix:

| action | manager | creator | other project member | token holder |
|---|---|---|---|---|
| view | yes | yes | yes | yes (unless revoked) |
| annotate | yes | yes¹ | no | no |
| take down / revoke | yes | yes | no | no |

¹ `can_annotate_hard_case` also requires `can_annotate_task` on the underlying
task. A case is not a separate document — annotating one writes the task's
working copy through the ordinary editor endpoints — so a creator whose task
was reassigned or locked drops to View-only rather than getting a button that
403s.

**Provider launchers** — `TaskProofreadingView` (→
`get_task_proofreading_info`), `TaskVisualizationView` (→
`get_visualization_state`, plus an `editable` flag from `can_annotate_task` —
so an approved-and-locked task reports view-only).

**Cellable-ported interactive AI tools** (Point Mask / Box Mask / Boundary /
Seeds, see `cellable_port/` below) — `TaskPredictMaskView` (`POST
/tasks/<id>/predict-mask/`, read-only mask preview), `TaskWarmEmbeddingView`
(`POST /tasks/<id>/warm-embedding/`, pre-computes/caches the encoder output
for a slice without predicting anything), `TaskWatershedView` (`POST
/tasks/<id>/watershed/`, writes the working copy), plus
`TaskLabelsSummaryView` / `TaskLabels3DView` backing the frontend's Labels
panel "All" scope and 3D preview.

**Slice streaming + label editor** — this is the performance/architecture-
sensitive surface, all built on `visualization/slice_io.py`:
- `VolumeMetaView` — `GET /api/volumes/<id>/meta/` → shape/dtype/axes/
  `display_range` (the volume-wide intensity normalization range, see
  `visualization/` below) + `has_label`.
- `VolumeSliceView` — `GET /api/volumes/<id>/slice/?axis=&index=[&window=&level=]`.
  **No `window`/`level`** (the default, what the current frontend sends) →
  JPEG, normalized against the volume's `display_range`, cached — one fetch
  per (axis, index) ever, brightness/contrast adjusted client-side
  afterward with zero more requests. **With** `window`/`level` (legacy
  path, still supported) → PNG, windowed server-side per request.
- `VolumeLabelSliceView` — `GET /api/volumes/<id>/label-slice/` — a
  colorized RGBA PNG overlay (read-only viewing; short cache, 15s, since
  labels change as people annotate).
- `TaskLabelStateView` — `GET /api/tasks/<id>/label-state/` →
  `{max_label_id, next_label_id}`, bootstraps the editor's "new instance"
  counter.
- `TaskLabelIdsView` — `GET`/`PUT /api/tasks/<id>/label-ids/` — the raw
  instance-id read/write the in-app editor actually paints against. RLE
  over the wire (`[[id, count], ...]`, row-major — see `slice_io.
  encode_label_rle`/`decode_label_rle`). `GET` needs `can_view_task`, `PUT`
  needs `can_edit_task`.
- `TaskTrackView` — `POST /api/tasks/<id>/track/`, body
  `{"seeds": [{z, rle, shape}], "z_range": [lo, hi]?}`. **Different RLE
  convention** from the label-ids endpoint above — `rle` here is
  true-runs-only (`[[start, length], ...]` marking where a boolean seed
  mask is `True`), decoded by `_decode_seeds`. Editors only (`can_edit_task`
  — requesters get 403, matching the view-only UI).
- `TaskPredictMaskView` — `POST /api/tasks/<id>/predict-mask/`, body
  `{axis, index, mode: "points"|"box"|"boundary", points?, point_labels?,
  box?}` → `{shape, runs}` (same 0/1 label-RLE convention as
  `TaskLabelIdsView`, reusing `encode_label_rle`). **Read-only** — never
  writes the working copy; the client merges the returned mask locally and
  commits through `TaskLabelIdsView`'s `PUT`, same as a brush stroke.
  Editors only. Returns `503` (not `500`) if the ported EfficientSAM model
  isn't installed/configured (`cellable_port.ai.registry.AiUnavailable`).
  Shares its embedding — in-process **and** on-disk (`cellable_port/ai/
  embed_cache.py`) — with `TaskWarmEmbeddingView` below, so a slice warmed
  ahead of time makes this call decoder-only.
- `TaskWarmEmbeddingView` — `POST /api/tasks/<id>/warm-embedding/`, body
  `{axis, index}` → `{"warmed": bool}`. Pre-computes (and caches) the
  EfficientSAM encoder output for one slice without running the decoder or
  returning a mask — called by the frontend on slice change / entering an
  AI tool (fire-and-forget), so the *first* real click only pays the
  decoder cost. A missing/unconfigured model reports `{"warmed": false}`
  with **`200`**, not `503` — warming is an optimization the UI should
  never treat as an error. Editors only (same gate as predict, since only
  editors ever call predict).
- `TaskWatershedView` — `POST /api/tasks/<id>/watershed/`, body `{label,
  seeds: [{z, y, x}, ...]}` → `{target_label, new_label_ids, bbox}`. Unlike
  the predict view above, this **does** write — a whole-volume
  read/mutate/write of the *working* copy, same shape as `track_task_fork`
  (see `run_watershed_task` below for why it reads the working copy, not
  the official label, unlike tracking). Editors only.
- `TaskLabelsSummaryView` — `GET /api/tasks/<id>/labels-summary/` →
  `{labels: [{id, voxel_count, z_start, z_end}, ...]}`, a whole-working-copy
  scan (cached by mtime — see `cellable_port/labels_3d.py`). Backs the
  Labels panel's "All labels" scope. Any role that can view the task.
- `TaskLabels3DMeshView` — `POST /api/tasks/<id>/labels-3d-mesh/`, body
  `{"labels": [...]}` → per-label **marching-cubes iso-surfaces** as a binary
  body (`_labels_3d_mesh_response`: a 52-byte header — version, mesh count,
  truncated count, geometry origin/size, physical `voxel_size` — then per
  mesh `int32 id`, vertex/triangle counts, `float32` (z,y,x) vertices and
  `uint32` indices; every field 4-byte aligned so the client can view it as
  typed arrays with no copy). This is what the 3D Labels panel renders, in
  Annotate, task View **and** the public share (`PublicHardCaseLabels3DMeshView`
  is the same function behind a token). Any role that can view the task.
- `TaskLabels3DView` — `GET /api/tasks/<id>/labels-3d/?labels=1,2,3` (or POST
  `{"labels": [...]}`) → the older downsampled voxel-grid body (little-endian
  `uint32 dz,dy,dx,num_labels` then per label `int32 label_id` + `dz*dy*dx`
  bytes of 0/1). **Legacy**: nothing in this app requests it any more; kept
  for older clients and as a fallback that needs no scikit-image.
- `TaskLabelLifecycleView` — `POST /api/tasks/<id>/labels/<label_id>/lifecycle/`,
  body `{"action": "verify"|"unverify"|"revert"|"reject"}` — Cellable-parity
  label lifecycle (Filters Options' Verify/Revert/Reject). Editors only. See
  `cellable_port/label_state.py` and `set_label_lifecycle_action` below.

## `cellable_port/` — code ported from local Cellable

See the package docstring (`cellable_port/__init__.py`) for the full
rationale and what's *not* ported (and why). Summary:

- `ai/efficient_sam.py` — `EfficientSam`, ported from `cellable/labelme/ai/
  efficient_sam.py`: ONNX encoder/decoder inference for point/box-prompt
  masks, same small-object cleanup (`skimage.morphology.remove_small_objects`
  — ported to that function's current `max_size` keyword, replacing the
  deprecated `min_size`, same ~5% cleanup intent), an in-process embedding
  LRU cache plus an optional on-disk one (`embed_cache.py` below) instead of
  Cellable's background-thread + fixed local-directory cache (not needed —
  a Django request is already synchronous, and `slice_io.py` already has
  this module's caching idiom; `EfficientSam.warm` is the explicit
  stand-in for "start a background thread on `set_image`", called from
  `services.warm_ai_embedding`).
  **Thread-count fix** (`_resolve_thread_count`/`_session_options`, not a
  Cellable mechanism — Cellable runs on one local desktop, this runs on a
  shared HPC node): both the encoder and decoder `InferenceSession`s get
  explicit `SessionOptions` with `intra_op_num_threads` resolved from
  `SLURM_CPUS_PER_TASK` → `os.sched_getaffinity(0)` → `os.cpu_count()`
  (capped at 8) instead of onnxruntime's own physical-core-count guess —
  fixes a real `pthread_setaffinity_np failed ... Invalid argument` ERROR
  flood under a cgroup-restricted SLURM allocation (harmless — predict
  still returned `200` — but noisy). `inter_op_num_threads=1` always,
  since encoder and decoder run sequentially within one request, never
  concurrently. See `progress/development.md`'s AI-tools section.
- `ai/embed_cache.py` — on-disk embedding cache, ported *idea* from
  `cellable/labelme/utils/pre_compute_tiff_sam_feature.py` (its
  `embedding_dir/slice_{i}.npy` files), adapted from "one file per slice
  index in a fixed local directory" to mito's multi-volume, multi-variant
  web backend. Now lives **beside the volume**, under its dataset folder's
  `embeddings/<variant>/` directory (not a global `MITO_DATA_ROOT/
  embeddings/` silo) — `services._ai_embedding_cache_path` resolves that dir
  + the volume's `working_mask_stem` via `label_paths` and passes them in, so
  this module stays pure path/IO (and its unit tests stay ORM-free). Keyed
  by embeddings dir + variant + **mask stem** + axis + index + the source
  image's mtime. The mtime keeps a variant swap or an image replacement from
  ever silently serving a stale embedding — the path simply stops matching, a
  clean miss, never a wrong hit; the stem keeps two volumes sharing one
  dataset folder from colliding. **The filename prefix MUST be
  `working_mask_stem` on both the write and the lookup side** — a mismatch
  (e.g. the migration once wrote the bare `image_stem` while the runtime read
  `working_mask_stem`) silently defeats the disk cache and makes *every*
  Point/Box click re-run the ~3s vits encoder. `migrate_volume_artifacts`
  now writes (and self-heals to) the same `working_mask_stem` prefix, and
  `test_cellable_port.py`'s `MigrateEmbeddingPrefixTests` guards it. No
  cleanup of orphaned old-mtime files (same cheap-to-regenerate tradeoff
  `slice_io.py`'s in-memory caches already make); wiped for free by
  `clear_dev_data`'s whole-data-root sweep.
- **Normalized-slice cache + timing** (`services.py`): `normalize_for_ai` is
  a full-slice percentile stretch (~300ms on a ~7MP EM slice) that was being
  recomputed on *every* warm and *every* predict click of the same slice.
  `services._normalized_ai_slice` bounds-LRU-caches it (keyed by image path/
  axis/index/mtime) so a warmed slice's later clicks skip that work — steady-
  state Point Mask is ~230ms (decode-bound), not ~480ms. Set
  `MITO_AI_TIMING=1` to log per-request timing (embed source
  inproc/disk/encoder, decode ms, predict prep/total) via the
  `mito.ai.timing` logger, so a cold-encoder-on-every-click regression is
  obvious.
- `ai/normalize.py` — `normalize_for_ai`, ported from `cellable/labelme/
  app.py`'s `normalizeImg`: stretches one 2D slice to uint8 from its
  **non-zero pixels'** 1st/99.5th percentile, per-slice — deliberately
  **not** `slice_io.display_range` (a whole-volume, display-stable stretch
  used for the JPEG/PNG streaming endpoints). Conflating the two was a real,
  independent-of-model-weight-tier source of point/box mask divergence from
  local Cellable, found and fixed while porting — see that module's
  docstring for the full explanation. `predict_ai_mask` uses this, not
  `display_range`.
- `ai/registry.py` — lazy singleton loader, mirrors
  `annotation/tracking/registry.py`'s provider pattern.
  `settings.MITO_CELLABLE_MODELS_ROOT` (default: the sibling cellable
  checkout's `labelme/models/` — **not vendored**, unlike `vendor/sam2/`,
  since these are plain ONNX weight files rather than a code dependency
  and Cellable lives on the same filesystem; override if that checkout
  moves) + `MITO_EFFICIENT_SAM_VARIANT` (**`vits`** default —
  "EfficientSam (accuracy)", matching Cellable's own default combo,
  `efficient_sam_vits_{encoder,decoder}.onnx`; a prior round defaulted to
  `vitt` (tiny/fast) as a CPU-friendliness shortcut, which was explicitly called out as not what
  "parity" means here — mask identity with local Cellable matters more than
  raw speed. Set `vitt` yourself if you explicitly want the smaller/faster
  model). Raises `AiUnavailable` (→ `503`, not a crash) if `onnxruntime`/
  `scikit-image`/`scipy` aren't installed (`environment.yml` —
  optional, CPU-only, same never-required-by-the-rest-of-the-app rule as
  `environment.yml`) or the model files aren't found.
- `label_state.py` — `LabelState`/`LabelOrigin`/`LabelMetadata`/
  `LabelMetadataStore`, ported from `cellable/labelme/label_state.py`: the
  Proposed/Edited/Verified lifecycle, JSON-sidecar persistence
  (`working_label_metadata_rel_path` in `label_paths.py` — mirrors
  Cellable's `sidecar_path` naming, `<mask>_metadata.json`, but kept in a
  `metadata/` subfolder beside the working mask). Same core rules as Cellable: a brand-new **manual** label
  starts EDITED (a human just drew it), a brand-new **automated**
  (AI/watershed/tracking) label starts PROPOSED (needs a look before it's
  trusted); re-editing *any* tracked label — including a VERIFIED one —
  always marks it EDITED again. Adapted for mito's per-slice-streamed
  backend: Cellable's snapshot (for `revert`) is a full-volume boolean mask
  (it holds everything in RAM); mito's is a single ``(z, RLE)`` slice — the
  only slice an AI-mask-created label can possibly exist on at the moment
  it's proposed. Watershed-created labels never get a snapshot (matching
  Cellable's own `store_snapshots=False` for watershed output) — only
  Point/Box/Boundary commits do. See `services.py`'s `set_label_slice_ids`
  (paint-time diff-based tracking), `run_watershed_task`, `track_task_fork`,
  `get_labels_summary` (merges lifecycle state into the voxel-count/z-range
  summary), and `set_label_lifecycle_action` (verify/unverify/revert/
  reject) for how the store gets read/written.
- `watershed.py` — `run_watershed_3d`/`label_bbox_3d`, ported from
  `cellable/labelme/app.py`'s `apply_3d_watershed`/`_label_bbox_3d`/
  `compute_bbox_3d`: bbox-crop around the target label, marker-based
  `skimage.segmentation.watershed` on the crop's distance transform,
  iteratively drop markers whose region comes out too small, relabel
  (largest region keeps the original id, everything else gets a new one).
  Qt bookkeeping (statusbar, the watershed undo/redo stack, 3D-cache
  invalidation) stripped — `services.py:run_watershed_task` is the
  Django-facing glue, and it deliberately reads/writes the volume's
  **working** copy directly (`tifffile.imread`/`_save_label_volume`), not
  `_load_or_init_label`'s official-label-seeded path that
  `track_task_fork` uses — Seeds/watershed exists to refine an instance the
  annotator is painting *this task*, so it must see already-painted,
  not-yet-submitted pixels; using the official-label fallback here would
  silently ignore any unapproved brush work.
- `labels_3d.py` — `label_summary` + `labels_3d_mesh` (and the legacy
  `labels_3d_preview` grid), backing `get_labels_summary` /
  `get_labels_3d_mesh` in `services.py`. Not a Cellable port (Cellable has
  the whole volume in RAM via `updateUniqueLabelListFromEntireMask` + a real
  VTK marching-cubes `VTKSurfaceWidget`) — everything here reads the working
  label file as a memmap, since a real EM label volume must never be loaded
  whole per request. `labels_3d_mesh` does produce true iso-surfaces now; see
  the module docstring and `progress/history/03-fix-hard-case-share-view.md`.
  - **The summary is per-slice statistics, summed.** The cache holds
    `{label: {z: (count, y1, y2, x1, x2)}}`; the "All labels" rows and every
    3D crop box are rolled up from it. Two things follow, and both matter:
    the whole-volume scan runs **per z-slice in parallel** (`_scan_stats`,
    threads — numpy releases the GIL; measured ~9.9s → ~1.1s on a
    137x2758x2514 volume), and a slice write folds into the cache instead of
    invalidating it (`update_summary_for_slice`, called by
    `set_label_slice_ids`). Before that fold-in, the `labels-summary` request
    that follows *every Save* re-scanned the volume: 10-27s of freeze on the
    volumes in this repo, now ~17ms.
  - Writers that change the volume wholesale (watershed / split / merge /
    tracking / lifecycle reject) call `forget_summary` instead — there is no
    single slice to fold in.
  - The fold-in refuses to apply when the file's mtime doesn't match what the
    cache was built from (another writer got in first); the next read then
    rescans. That is the multi-writer safety property — see
    `progress/history/04-perf-repo-hygiene-oneclick-setup.md`.

**Not ported**: `cellable/labelme/utils/compute_points_from_mask.py` — only
used upstream by Cellable's multi-slice AI-mask propagation
(`predictNextNSlices`), which mito doesn't replicate for the interactive
tools (deliberately single-slice; multi-slice propagation is what the
fork-aware SAM2 `tracking/` below already does). Porting a point-
re-derivation helper with no call site would be dead code — see the
package docstring if this changes later.

## `visualization/slice_io.py` (478 lines) — the streaming/caching core

Not a Django model or view — pure numpy/tifffile IO with module-level LRU
caches. Five caches, each with a distinct purpose (see the module
docstring for the full rationale):

| Cache | Holds | Why separate |
|---|---|---|
| `_volume_cache` | Open **read-only** memmaps (`_open_volume`) | Images are never mutated; keyed by `(path, mtime)` so a file change (new mtime) transparently reopens. |
| `_slice_cache` | Decoded 2D numpy slices | Revisiting a slice while scrubbing is instant. |
| `_range_cache` | Per-volume `display_range` (lo, hi) | Computed once (sampled percentiles for non-uint8 data), reused for every slice of that volume so brightness is stable while scrubbing. |
| `_encoded_cache` | Final JPEG/PNG **bytes** | A revisited slice costs nothing to re-encode, not just re-decode — encoding (esp. the old hand-rolled PNG path) was measurably expensive. |
| `_label_volume_cache` | Open **writable** memmaps (`open_label_volume_writable`) | Small (cap 4) — these are actively edited, kept open across requests so repeated strokes on one task don't pay a re-open cost. |
| `_label_max_cache` | Per-file cached max instance id | Avoids an O(volume) `.max()` scan on every stroke. |

`clear_caches()` nukes everything (used after a full-volume rewrite, e.g.
`track_task_fork`). `invalidate_read_caches()` clears only `_slice_cache`/
`_encoded_cache` — used after a per-slice paint commit, deliberately
**leaving the writable memmap and max-id caches warm** (re-opening those on
every stroke would reintroduce the cost this module exists to avoid).

**Corrupt-label recovery (long-run stability).** A working label file can
end up non-memmapable/header-corrupt (the real "image data are not
memory-mappable" incident — a partial write left a ~1.9GB file with empty
`dataoffsets`). Three pieces keep a long Annotate session up instead of
falling into a sticky 500 loop:
- `_quarantine_corrupt(path)` moves a broken file aside to `<name>.corrupt.
  bak` (never a first-step `unlink` — a Weka snapshot once saved a user's
  labels after an erroneous delete), only removing an *older* bak to make
  room. `open_label_volume_writable` uses it: on a corrupt/wrong-shape/empty
  file it salvages voxels via `imread` when possible, quarantines the old
  file, then atomically rebuilds a memmap-compatible copy (`.tmp` → replace,
  caches cleared first) — the hot per-slice path recovers transparently.
- `read_label_array(path)` is the recovering whole-volume reader for the
  *rare* mutators (watershed/split/merge/tracking): memmap → `imread` →, if
  both fail, quarantine + raise `SliceIOError` (a clean recoverable error,
  not an uncaught 500). `_load_or_init_label` (which seeds from the possibly
  *external* official label) instead just falls back to zeros on a bad read —
  it must never quarantine a file this app doesn't own.
- **API resilience**: the label endpoints (`labels-summary`, `labels-3d`,
  `labels-3d-mesh`,
  `label-ids` GET/PUT, `label-state`, watershed/split/merge/track/lifecycle)
  catch `(ValueError, SliceIOError, OSError)` and return JSON 400 with a
  message the UI can show, never leaking a Django debug 500. Covered by
  `test_cellable_port.py`'s `WorkingLabelRecoveryTests` (unit) and
  corrupt-working-copy API tests.

**Format handling** (`_open_volume`): `.tif`/`.tiff` via `tifffile.memmap`
(falls back to full `tifffile.imread` only if memmap fails — e.g. a
compressed TIFF can't be memory-mapped; check this if a *new* volume feels
slow to open even after the memmap fixes). `.npy` via `np.load(mmap_mode="r")`.
`.nii`/`.nii.gz` via nibabel, transposed from its native `(X,Y,Z)` to this
app's `(Z,Y,X)` convention. `.h5`/`.hdf5` via `visualization/hdf5_io.py` —
read **in place**, never transcoded to a TIFF copy: a chunked HDF5 dataset
already supports "read one plane, touch nothing else", which is the only
property this module needs from a source. Read that module's docstring
before changing it — the chunk-cache sizing and `locking=False` are both
load-bearing, and HDF5 is deliberately source-only (labels stay TIFF
memmaps, see below). An h5 file can hold several datasets, so which one is
the volume is decided by conventional name → the file's only 3-D dataset →
otherwise a hard error naming the candidates; `MITO_HDF5_DATASET` overrides.
Covered by `test_hdf5_source.py`, which mostly asserts h5 and tif agree.

**Encoding**: `encode_png` is a small dependency-free PNG writer (no
Pillow) used for label overlays (need lossless + alpha transparency, and
they're usually mostly-transparent so compress well anyway). `encode_jpeg`
uses Pillow/libjpeg-turbo for intensity slices — ~2x smaller and ~9x faster
to encode than the hand-rolled PNG path for photographic-style EM data.

**Label RLE**: `encode_label_rle`/`decode_label_rle` — full-coverage,
row-major RLE of a label slice's raw instance ids. Distinct from the
tracking endpoint's true-runs-only seed RLE (`_decode_seeds` in `api.py`)
— don't conflate the two formats.

## `tracking/` — fork-aware SAM2 tracking

Ports the "multi-branch" idea from an external `MTS` codebase: when a
mitochondrion **forks** in a seed mask, each 8-connected branch gets its own
temporary track id so the tracking provider can propagate them
independently, but they're kept in one logical group and **auto-merged**
back into a single final instance afterward — a fork never permanently
splits one mitochondrion into two unless the user explicitly wants that.

- `branching.py` — pure numpy, provider-agnostic: `split_binary_mask_
  components` (8-connected components of a seed mask), `TrackGroup`,
  `merge_group`, `next_free_id`.
- `services.py:run_branch_tracking(image, volume_mask, seeds, z_range,
  provider=None)` — the orchestration: split → provider propagates each
  branch → merge. Mutates `volume_mask` in place, returns
  `{final_id, branch_ids, group}`.
- `interfaces.py`/`registry.py` — `TrackingProvider` ABC + `local`/`sam2`
  selection via `MITO_TRACKING_PROVIDER`. `local` is a dependency-free CPU
  stand-in for dev/CI; `sam2` runs the real GPU model (only meaningful on a
  GPU compute node — this is the one place in the whole app a GPU actually
  matters).
- `adapters/sam2_bridge.py` — the actual SAM2 video-predictor wrapper
  (`SAM2Wrapper`), a self-contained port of `MTS/mts_mask_editor/core/
  sam2_wrapper.py` with the one hardcoded bit (`SAM2_ROOT` pointing at a
  sibling `MTS` checkout) turned into a constructor argument
  (`sam2_root`, sourced from `settings.MITO_SAM2_ROOT`, which now defaults
  to the vendored copy at `vendor/sam2/` — see root `README.md`). Fixed a real bug found while
  porting: the original's `propagate_multi` merge loop referenced a stale
  `oid` left over from a separate loop instead of iterating per-object, so
  it only ever merged one arbitrary branch's masks — now actually nested.
  `adapters/sam2.py`'s `_load()` imports this module and instantiates
  `SAM2Wrapper` directly; no more `sys.path`-injecting an external
  `mts_sam2_bridge` module that didn't exist anywhere in this repo.

## Provider interfaces (`proofreading/`, `visualization/`, `quality_control/`, `publishing/`)

Each follows the same shape: `interfaces.py` (an ABC + a small dataclass
for the return value), `registry.py` (`get_X_provider(name=None)`, reading
a `MITO_X_PROVIDER` setting), `adapters/` (concrete implementations).

- **Proofreading** (`ProofreadingProvider`, `LaunchInfo`) — how an
  annotator opens a task. `mode` is `edit`/`view`/`download`/`unavailable`;
  `editable` must be honest — a provider reporting a read-only viewer must
  set `editable=False` so the app never implies edits are possible when
  they aren't. Default adapter: `inapp` (`adapters/inapp.py`) — reports
  `mode="edit"`, `url=/editor/tasks/<id>`, which is the React
  `AnnotationCanvas`. Also `external_tool`, `neuroglancer`, `placeholder`.
- **Visualization** (`VisualizationProvider`) — how *anyone* views a
  volume/task (broader than proofreading — includes read-only viewers).
  Default: `inapp` → the React `SliceViewer`.
- **Quality control** (`quality_control/`) — `run_basic_qc` (called from
  `services.submit_annotation`) is currently the only real implementation
  (`basic` provider); the interface exists for future QC backends.
- **Publishing** (`publishing/`) — interface exists, only a `placeholder`
  adapter — no real publishing pipeline wired up yet.

## Tests

`test_tracking.py` is the most relevant one to read if you're changing the
label-editor backend — it exercises the RLE round trip, role gating on
every slice/label/tracking endpoint, and (as of the round-2 performance/
safety work) the regression test proving an edit never mutates an
externally-referenced label file
(`test_edit_never_mutates_an_externally_referenced_label_file`). Uses a
`tempfile.mkdtemp()` + `@override_settings(MITO_DATA_ROOT=...)` fixture
pattern — **follow this pattern for any new destructive test**; testing
against the real dev database is what caused a real data-loss incident once.

`test_api_flows.py`/`tests.py` cover the broader task/submission/review/
assignment flows. `test_providers.py` covers the provider registries.

`test_cellable_port.py` covers the Cellable-ported tools: pure-numpy
watershed unit tests (including a real dumbbell-split case), the labels
summary/3D-preview cache-layer unit tests, and role-gated API tests for
`predict-mask`/`watershed`/`labels-summary`/`labels-3d` (including a real
EfficientSAM prediction against a synthetic bright-square image — skipped
automatically if the ONNX weights aren't present in the environment, and a
separate test forcing `MITO_CELLABLE_MODELS_ROOT` to a bad path to assert
the `AiUnavailable` → `503` degradation, not a `500`), plus the label
lifecycle: a brand-new manual label starts EDITED, a brand-new AI label
starts PROPOSED with a revertible snapshot, re-editing a VERIFIED label
puts it back to EDITED, verify/unverify, revert (restores only the
originally-snapshotted slice, discarding any pixels painted elsewhere since)
and its 400 when there's no snapshot, reject (deletes every voxel + drops
metadata), role gating on the lifecycle endpoint, and watershed registering
new ids as PROPOSED/WATERSHED with no snapshot while marking the target
EDITED. Also covers `_resolve_thread_count`'s
SLURM/cap/garbage-input handling, the embedding cache's round trip and
variant/mtime key-uniqueness (both isolated in their own
`@override_settings(MITO_DATA_ROOT=...)`-wrapped test class — writing
cache files under the *real* data root from a test is exactly the mistake the data-safety rule exists to prevent, and an earlier draft of
this suite did exactly that before being caught and fixed), and
`warm-embedding` actually populating the disk cache such that a subsequent
predict still returns the correct mask (skipped automatically if the ONNX
weights aren't present) plus its `{"warmed": false}`/`200` degradation when
the model isn't configured. Same tempdir + `@override_settings(MITO_DATA_ROOT=...)`
fixture pattern as `test_tracking.py` throughout — same data-safety reasoning.
