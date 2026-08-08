# Code map — "I want to change X, where do I go?"

This is the quick lookup for **which files own which feature**. Paths are
relative to the repo root. Read [architecture.md](architecture.md) first if you
haven't — the one thing to remember is that **business logic lives in
`<app>/services.py`**, and views/admin/pages are thin wrappers around it.

## One replaceable feature → one folder

Each integration lives behind a small provider interface (`interfaces.py` +
`registry.py` + `adapters/`), selected by a `settings.MITO_*` value. The domain
services call the registry; **admin/API/React never import an adapter directly.**

```
I want to change online proofreading:
→ backend/annotation/proofreading/           (interface, registry, adapters/)
→ frontend/src/features/proofreading/

I want to change QA / quality control:
→ backend/annotation/quality_control/         (adapters/basic.py is the default)

I want to change visualization (e.g. Neuroglancer):
→ backend/annotation/visualization/           (interfaces, registry, adapters/, slice_io.py)
→ frontend/src/features/viewer/SliceViewer.tsx (in-app slice viewer)

I want to change slice streaming / caching (memory):
→ backend/annotation/visualization/slice_io.py (memmap + bounded LRU + PNG;
   `invalidate_read_caches(path)` / `drop_file(path)` are per-file on purpose —
   a bare clear evicts every other user's warm slices)
→ frontend/src/features/viewer/AnnotationCanvas.tsx (client-side z cache +
   neighbour prefetch: `sliceImageUrl` / `labelRunsFor`)

I want to make the Labels panel / "All" list faster:
→ backend/annotation/cellable_port/labels_3d.py (`label_summary` per-slice
   stats, parallel scan, `update_summary_for_slice` fold-in on save)
→ backend/annotation/services.py (`set_label_slice_ids` calls the fold-in;
   whole-volume writers call `forget_summary`)
→ frontend/src/features/viewer/useVirtualRows.ts + LabelsPanel.tsx (windowing)

I want to change volume shape / voxel-size detection:
→ backend/core/utils.py (`inspect_volume_shape` / `inspect_volume_voxel_size`,
   OME-XML + ImageJ + resolution tags, all normalised to µm, mtime-cached)
→ backend/annotation/services.py `_render_voxel_size` (what 3D scales by)

I want to change the 3D Labels view (surfaces, meshing, progress):
→ backend/annotation/cellable_port/labels_3d.py (marching cubes, per-label
   bbox crops, per-axis strides, mesh cache)
→ backend/annotation/api.py `_labels_3d_mesh_response` (binary wire format)
→ frontend/src/features/viewer/Labels3DPanel.tsx (three.js render + progress)
→ frontend/src/api/viewer.ts `decodeLabels3DMesh`

I want to change hard cases (project list, inbox, take-down, public link):
→ backend/annotation/models.py `HardCase` + services.py (`create_hard_case`,
   `visible_hard_cases`, `can_annotate_hard_case`, `set_hard_case_status`)
→ backend/annotation/api.py (`HardCase*View`, `PublicHardCase*View`)
→ frontend/src/pages/{HardCasesPage,HardCaseDetailPage,HardCaseSharePage}.tsx
→ frontend/src/components/HardCaseList.tsx + api/hardCases.ts
→ progress/history/{02-share-hard-case,03-fix-hard-case-share-view,05-submit-people-hardcases}.md

I want to change the submit / approve loop (who can submit, latest-only, lock):
→ backend/annotation/services.py (`can_submit_task`, `can_annotate_task`,
   `_supersede_submissions`, `approve_submission`, `set_task_annotation_lock`)
→ backend/annotation/serializers.py (`can_submit`/`can_annotate` on the task)
→ frontend/src/pages/{ViewerPage,TaskDetailPage,ReviewSubmissionPage}.tsx
→ progress/history/05-submit-people-hardcases.md

I want to change People (who works with whom, profiles):
→ backend/accounts/services.py (`people_overview` and the project↔people helpers)
→ backend/accounts/api.py (`PeopleOverviewView`, `MyProfileView`, `PersonDetailView`)
→ frontend/src/pages/{PeoplePage,PersonPage}.tsx + api/people.ts

I want to change the Annotate editor (tools, AI propose loop, tracking, layout, hotkeys):
→ frontend/src/features/viewer/AnnotationCanvas.tsx   (the editor; shared by task View + Annotate via `editable`)
→ frontend/src/features/viewer/annotate/            (AnnotateToolChrome, TrackRail, paintTools)
→ frontend/src/features/viewer/{LabelsPanel,Labels3DPanel}.tsx
→ frontend/src/styles.css   (.canvas-main-row grid, .track-rail, .canvas-status-overlay, .labels-row-size)
→ backend/annotation/cellable_port/ai/{efficient_sam,embed_cache,normalize}.py   (EfficientSAM predict)
→ /projects/weilab/shenb/cellable/labelme/          (the Cellable reference this is ported from)
→ see frontend/features/MODULE.md for the full current-state walkthrough

I want to change SAM2 tracking / forked-mito merge behaviour:
→ backend/annotation/tracking/                 (branching.py, services.py, adapters/{local,sam2})
→ frontend/src/pages/ViewerPage.tsx + frontend/src/api/viewer.ts
→ also read cellable labelme/app.py tracking + labelme/ai/ when porting UX

I want to change who can view vs annotate:
→ backend/annotation/services.py               (can_view_task / can_edit_task / can_view_volume)

I want to change SLURM / HPC submission:
→ backend/processing/adapters/slurm.py
   (local/mock: backend/processing/adapters/local.py)

I want to change how processing jobs are dispatched:
→ backend/processing/services.py
→ backend/processing/management/commands/run_processing_dispatcher.py

I want to change publication (MitoVerse / Hugging Face):
→ backend/annotation/publishing/

I want to change the New / To Proofread / Done mapping:
→ backend/core/lifecycle.py
→ frontend/src/features/lifecycle/

I want to change display terminology (Requester → Institution, etc.):
→ backend/core/labels.py
→ frontend/src/labels.ts
```

Provider selection settings (all in `config/settings.py`, overridable via env):
`MITO_QC_PROVIDER`, `MITO_PROOFREADING_PROVIDER`, `MITO_VISUALIZATION_PROVIDER`,
`MITO_PUBLISHING_PROVIDER`, `MITO_PROCESSING_BACKEND`.

---

If you only remember one table, remember this one:

| I want to change… | Go to |
| --- | --- |
| **What the system does** (rules, calculations, workflow) | `backend/<app>/services.py` |
| **How the REST API exposes it** | `backend/<app>/api.py` + `serializers.py`, route in `backend/config/urls.py` |
| **How the Manager Admin exposes it** | `backend/<app>/admin.py` (+ `backend/templates/admin/...`) |
| **A database field** | `backend/<app>/models.py` → `makemigrations` → update serializer/admin/frontend type |
| **A React screen** | `frontend/src/pages/*` (+ route in `frontend/src/routes/AppRoutes.tsx`) |
| **Shared enums / statuses / roles** | `backend/core/choices.py` (mirror in `frontend/src/types/index.ts`) |

---

## Backend — by feature

### Accounts, roles, auth
| Feature | File(s) |
| --- | --- |
| Roles list (manager/annotator/requester/…) | `backend/core/choices.py` (`UserRole`) |
| Role helpers (`is_manager`, `is_requester`, `can_register_data`) | `backend/accounts/roles.py` |
| DRF permission classes (`IsManager`, `CanRegisterData`, …) | `backend/core/permissions.py` |
| User / profile fields, annotator capacity | `backend/accounts/models.py` (`UserProfile`, `AnnotatorProfile`, `Institution`) |
| Public registration rules (annotator/requester only) | `backend/accounts/serializers.py` (`RegisterSerializer`) |
| Login + login-portal validation | `backend/accounts/serializers.py` (`LoginSerializer`), `backend/accounts/api.py` |
| Auto-create a profile on signup | `backend/accounts/signals.py` |
| Auth/registration/annotator-list endpoints | `backend/accounts/api.py` |

### Projects, approval, progress
| Feature | File(s) |
| --- | --- |
| Project fields (dataset, metadata, review state, status, deadline) | `backend/projects/models.py` |
| Create a project / project defaults | `backend/projects/services.py` (`create_project`) |
| Manager approval (review gate) | `backend/projects/services.py` (`mark_project_reviewed`) |
| Progress calculation (%, counts) | `backend/projects/services.py` (`calculate_project_progress`) |
| Project REST endpoints + `review`/`summary` actions | `backend/projects/api.py`, `backend/projects/serializers.py` |

### Data registration & volumes
| Feature | File(s) |
| --- | --- |
| Supported data extensions (`.tif/.tiff/.nii.gz`) | `backend/volumes/services.py` (`SUPPORTED_DATA_EXTENSIONS`) |
| Register a dataset (dataset/volume/pairs/metadata) | `backend/volumes/services.py` (`register_dataset`) |
| Scan an HPC directory / validate the path | `backend/volumes/services.py` (`scan_hpc_directory`, `resolve_hpc_directory`) |
| Image + mask auto-pairing rules | `backend/volumes/services.py` (`detect_volume_pairs`, `MASK_TOKENS`/`IMAGE_TOKENS`) |
| Volume fields (paths, label type, shape, format) | `backend/volumes/models.py` |
| Derive image shape from a file | `backend/core/utils.py` (`inspect_volume_shape`, `read_tiff_shape_fast`) |
| Registration/scan REST endpoints | `backend/volumes/api.py`, `backend/volumes/serializers.py` |

### Tasks, assignment, submissions, review
| Feature | File(s) |
| --- | --- |
| Task / submission / review fields & statuses | `backend/annotation/models.py` + `backend/core/choices.py` (`TaskStatus`, `TaskType`, `ReviewDecision`) |
| Task-type inference from label state | `backend/volumes/services.py` (`infer_task_type`; map in `core/choices.py`) |
| Whole-volume task creation | `backend/annotation/services.py` (`create_whole_volume_task`, `ensure_volume_tasks`) |
| **Auto-assign** (even distribution, capacity) | `backend/annotation/services.py` (`auto_assign_project`, `assign_tasks_rule_based`) |
| **Manual** assign / reassign / unassign | `backend/annotation/services.py` (`assign_task_to_annotator`), `backend/annotation/api.py` |
| Submission + basic QC (allowed label extensions) | `backend/annotation/services.py` (`submit_annotation`, `run_basic_qc`); `MITO_ALLOWED_LABEL_EXTENSIONS` in `config/settings.py` |
| Review decisions (approve/reject/revision) | `backend/annotation/services.py` (`review_submission` + helpers) |
| Annotator workload metrics | `backend/annotation/services.py` (`calculate_annotator_workload`) |
| Task/submission/review REST endpoints | `backend/annotation/api.py`, `backend/annotation/serializers.py` |

### Manager Admin (the managers' portal)
| Feature | File(s) |
| --- | --- |
| Admin identity, access rule, dashboard metrics | `backend/core/admin_site.py` |
| Installing the custom admin site | `backend/core/admin_apps.py` (+ `INSTALLED_APPS` in `config/settings.py`) |
| Admin permission mixin + link helpers | `backend/core/admin_common.py` |
| A specific model's admin screen / bulk actions | `backend/<app>/admin.py` |
| Intermediate action forms (assign / review-with-comment) | `backend/templates/admin/annotation/*.html` + the action in `backend/annotation/admin.py` |
| Dashboard layout | `backend/templates/admin/manager_index.html` |

### Lifecycle, workflow types, providers, processing
| Feature | File(s) |
| --- | --- |
| New / To Proofread / Done mapping (classify project/volume/task, counts, filter) | `backend/core/lifecycle.py` |
| Workflow type (annotation / proofreading / segmentation) | `backend/core/choices.py` (`WorkflowType`), `backend/projects/models.py` (`workflow_type`), `projects/services.py` (`resolve_workflow_type`) |
| Display terminology (Requester → Institution) | `backend/core/labels.py` (mirror `frontend/src/labels.ts`) |
| Lifecycle REST: `?lifecycle=` filter, `/api/projects/lifecycle-counts/` | `backend/projects/api.py` |
| Lifecycle admin filter + dashboard metrics | `backend/projects/admin.py` (`LifecycleFilter`), `backend/core/admin_site.py` |
| QC provider (default = basic file checks) | `backend/annotation/quality_control/` |
| Proofreading provider (launch/download; view vs edit) | `backend/annotation/proofreading/`; service `get_task_proofreading_info` |
| Visualization provider (Neuroglancer viewer) | `backend/annotation/visualization/`; service `get_visualization_state` |
| Publishing provider (placeholder / MitoVerse stub) | `backend/annotation/publishing/` |
| ProcessingJob model / fields | `backend/processing/models.py` + `core/choices.py` (`ProcessingJob*`) |
| Processing backends (local / SLURM) | `backend/processing/adapters/{local,slurm}.py`, registry `processing/registry.py` |
| Job creation / dispatch / retry / cancel | `backend/processing/services.py` |
| Dispatcher command | `backend/processing/management/commands/run_processing_dispatcher.py` |
| Processing REST + admin | `backend/processing/api.py`, `backend/processing/admin.py` |
| Proofreading/visualization task endpoints | `backend/annotation/api.py` (`TaskProofreadingView`, `TaskVisualizationView`) |

### Plumbing
| Feature | File(s) |
| --- | --- |
| Backend URL routes | `backend/config/urls.py` |
| Settings / env vars / installed apps | `backend/config/settings.py` (+ `.env`, `.env.example`) |
| File storage location & path handling | `backend/core/storage.py` |
| API landing page at `:8000/` | `backend/config/views.py` |

---

## Frontend — by feature

| Feature | File(s) |
| --- | --- |
| Feature modules (replaceable UI features) | `frontend/src/features/{lifecycle,proofreading}/` |
| New/To Proofread/Done tabs + counts | `frontend/src/features/lifecycle/` |
| "Open Proofreading Tool" / download descriptor | `frontend/src/features/proofreading/` |
| Display labels (Institution, lifecycle, workflow) | `frontend/src/labels.ts` |
| Add / change a page | `frontend/src/pages/*` |
| Route table & role-based redirects | `frontend/src/routes/AppRoutes.tsx` (`effectiveRole`, `homePathForRole`) |
| Top navigation (per role) | `frontend/src/components/Navbar.tsx` |
| Auth state, login/register/logout, `isManager`/`isRequester` | `frontend/src/auth/AuthContext.tsx` + `frontend/src/api/auth.ts` |
| Talking to the backend (fetch wrapper, token) | `frontend/src/api/client.ts` |
| Per-resource API calls | `frontend/src/api/{projects,volumes,tasks,submissions,registerData}.ts` |
| TypeScript types mirroring the backend | `frontend/src/types/*` |
| Login tabs (requester/annotator portals) | `frontend/src/pages/LoginPage.tsx` |
| Public sign-up | `frontend/src/pages/RegisterPage.tsx` |
| Register Data (dataset/volume/dir scan/pairs/metadata) | `frontend/src/pages/RegisterDataPage.tsx` + `frontend/src/api/registerData.ts` |
| Requester dashboard | `frontend/src/pages/RequesterDashboard.tsx` |
| Annotator dashboard / my tasks | `frontend/src/pages/AnnotatorDashboard.tsx` + `frontend/src/components/TaskTable.tsx` |
| Manager views in the SPA (project detail, assignment table, metadata) | `frontend/src/pages/{ManagerDashboard,ProjectDetailPage}.tsx`, `frontend/src/components/{TaskAssignmentTable,MetadataCard}.tsx` |
| Task detail / submit a label | `frontend/src/pages/{TaskDetailPage,SubmitTaskPage}.tsx`, `frontend/src/components/FileUpload.tsx` |
| Review a submission (SPA) | `frontend/src/pages/ReviewSubmissionPage.tsx` |
| Volume detail / manual split UI | `frontend/src/pages/VolumeDetailPage.tsx` |
| Status pills / colors | `frontend/src/components/StatusBadge.tsx` |
| Global styling / theme | `frontend/src/styles.css` |
| Dev-only login helper (seed accounts) | `frontend/src/pages/LoginPage.tsx` (`import.meta.env.DEV` block) |

---

## Command-line

| Feature | File(s) |
| --- | --- |
| Dev accounts seed/clear/reset/status | `backend/core/dev_data.py` + `backend/core/management/commands/{seed_dev,clear_dev_data,reset_dev,dev_status}.py` |
| Rule-based assignment (CLI) | `backend/annotation/management/commands/assign_tasks.py` |
| Project progress report (CLI) | `backend/projects/management/commands/progress_report.py` |

---

## Common recipes (end-to-end)

**Add a database field** → edit `backend/<app>/models.py` → `python manage.py
makemigrations && migrate` → expose it in `serializers.py` (API) and/or
`admin.py` (admin) → add it to `frontend/src/types/*` and the page/component that
shows it.

**Add a REST endpoint** → put the logic in `services.py` → add a
`serializers.py` shape → add the view in `api.py` → wire the route in
`config/urls.py` → add a caller in `frontend/src/api/*.ts` → use it from a page.

**Add a Manager Admin action** → put the logic in `services.py` → add an
`@admin.action` in `backend/<app>/admin.py` (add a template under
`backend/templates/admin/...` if it needs an intermediate form) → cover it in
`backend/core/test_admin.py`.

**Add a React page** → create `frontend/src/pages/MyPage.tsx` → register the
route in `frontend/src/routes/AppRoutes.tsx` → add a nav link in
`frontend/src/components/Navbar.tsx` → add API calls in `frontend/src/api/*.ts`.

**Change a role/permission** → `core/choices.py` (the role) → `accounts/roles.py`
(helper) → `core/permissions.py` (DRF) and/or `core/admin_common.py` (admin) →
`frontend/src/auth/AuthContext.tsx` + `AppRoutes.tsx` (frontend gating).

## Where the tests live

| Scope | File |
| --- | --- |
| Services & workflow (assignment, QC, review, progress) | `backend/annotation/tests.py`, `backend/volumes/tests.py` |
| REST API flows | `backend/annotation/test_api_flows.py`, `backend/accounts/tests.py` |
| Manager Admin (access, actions, audit) | `backend/core/test_admin.py` |
| Dev-data commands | `backend/core/tests.py` |
| Frontend | type-checked by `npm run build --prefix frontend` (`tsc`) |
