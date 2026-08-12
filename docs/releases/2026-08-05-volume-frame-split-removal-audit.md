# Removal of volume → multi-task frame splitting — audit

**Date:** 2026-08-05
**Trees:** `/home/weidf/shenb/mito-data-studio` (development, primary),
`/home/weidf/shenb/mito-data-studio-production-v1.1.1` (production, aligned +
redeployed)
**Not pushed.** Both trees hold the change as working-tree edits only.

## Invariant enforced

One volume is one assignable work unit. A volume is never subdivided into
several frame/z-range annotation tasks. `AnnotationTask` remains the assignable
unit, and `z_start`/`z_end` remain on the model as **full-volume extent
metadata** for the viewer — always `0 .. shape_z`.

## 1. rg evidence that split residue is gone

Pattern set: `z_step|zStep|Z_STEP|create_tasks_from_volume|split_volume|
VolumeSplit|splitVolume|split_into_frame|split_volume_by_frames|Split volume`.

Both trees now return only **deliberate** hits:

| Hit | Why it stays |
| --- | --- |
| `backend/volumes/tests.py:252-254` | guard test asserting the three service helpers no longer exist |
| `backend/core/test_admin.py:165-166` | guard test asserting the admin action is gone |
| `backend/annotation/tests.py:334` | guard test POSTing the removed endpoint and asserting 404 |
| `progress/backend/volumes/MODULE.md:115-116` (dev only) | rewritten doc note recording the removal |

Nothing else matches in either tree. The built production bundle
(`frontend/dist/assets/index-DLiNsN_k.js`) and the rebuilt development Docker
image both contain **zero** matches for `Split volume`, `z_step`, `splitVolume`,
or `/split/`.

### Deleted

| Kind | Path |
| --- | --- |
| Service | `volumes.services.split_volume_by_frames` |
| Service | `volumes.services.create_tasks_from_volume` |
| Service | `volumes.services.split_volume_into_tasks` |
| Serializer | `volumes.serializers.VolumeSplitSerializer` |
| View | `volumes.api.VolumeSplitView` |
| URL | `api/volumes/<int:pk>/split/` in `config/urls.py` |
| Command | `backend/volumes/management/commands/split_volume.py` (file removed) |
| Admin action | `split_into_frame_tasks` in `volumes/admin.py` |
| Setting | `MITO_DEFAULT_Z_STEP` (`config/settings.py`, `.env.example`) |
| Enum | `core.choices.VolumeStatus.SPLIT` + its `core/lifecycle.py` mapping |
| Frontend API | `splitVolume` / `SplitInput` in `frontend/src/api/volumes.ts` |
| Frontend UI | "Split into frame-based tasks" card, `zStep`/`taskType`/`doSplit` in `VolumeDetailPage.tsx` |
| Frontend type | `Volume.task_count` in `frontend/src/types/volume.ts` |

### Kept (unchanged, by design)

`AnnotationTask`; `annotation.services.create_whole_volume_task` and
`ensure_volume_tasks`; the Assign tab, assignment plan editor, auto-assign,
submit/review, shares, hard cases; `volumes.services.infer_task_type`;
**project-level** `task_count` on dashboards, project list, people and
collaboration impact (that still means "assignable units in this project");
the Django admin's per-volume Tasks/Active/Completed columns (0-or-1 now reads
as "does this volume have its task yet", which is still useful to an operator);
the unrelated 3-D label **Split** annotation tool; the dataset `train`/`test`
`split` metadata field.

## 2. Assign still creates and uses one task per volume

`rg 'AnnotationTask.objects.create|bulk_create|AnnotationTask\('` over all
non-test backend code returns **exactly one** creation site:
`backend/annotation/services.py:292`, inside `create_whole_volume_task`. Every
manager-reachable path funnels through it:

- `ensure_volume_tasks` → `create_whole_volume_task` (skips volumes that
  already have a task — duplicate-safe)
- `preview_assign_project` → `ensure_volume_tasks` (`services.py:211`)
- `apply_assignment_plan` / assign-tab ensure-on-open (`services.py:159`)
- `auto_assign_project` → `ensure_volume_tasks` (`services.py:349`)
- Django admin action `create_whole_volume_tasks`

There is no longer any code path that can produce a second task for a volume.

### New hard-invariant test

`annotation.tests.OneTaskPerVolumeInvariantTests`:

- `test_every_creation_path_leaves_one_task_per_volume` — runs
  `ensure_volume_tasks`, `preview_assign_project` and `auto_assign_project`
  twice in sequence over three volumes, and after each round asserts every
  volume has **at most one** task spanning its full `z`/`y`/`x` extent.
- `test_split_endpoint_is_not_registered` — `reverse("api-volume-split")`
  raises `NoReverseMatch`, and a manager POST to `/api/volumes/<id>/split/`
  returns 404 while creating no task.

Plus guard tests in `volumes.tests` (services removed) and `core.test_admin`
(admin action removed).

## 3. UI

- **Data tab** — the per-volume **Tasks** column is gone from
  `DatasetsCard.tsx` and `ProjectDetailPage.tsx`'s `VolumeList`, and the
  `tasks` slot was removed from the shared `DatasetVolumesTable`
  (`VolumeMeta.tsx`) plus its `.col-meta-tasks` CSS rule. It rendered
  `undefined tasks` in production because `VolumeSerializer` never returned
  `task_count` — confirmed against the live API in §5.
- **Volume detail** — no "Split volume" control, no z-step input, no task-type
  override. The empty state now points managers at the Assign tab.
- **Login page** — marketing copy no longer invites "split them into z-range
  tasks".
- No API client calls the removed endpoint (`splitVolume` deleted; zero
  `/split/` occurrences in the shipped bundle).

## 4. Test results

| Command | Result |
| --- | --- |
| dev: `manage.py test volumes annotation core` (conda env) | **1015 tests, 5 errors, 60 skipped** — all 5 errors are `ModuleNotFoundError: h5py` / `nibabel`, pre-existing optional deps missing from the conda env, in tests untouched by this work |
| dev: `manage.py test volumes.tests annotation.tests annotation.test_api_flows core.test_admin volumes.test_nifti volumes.test_pyramid_build volumes.test_region_mask_coverage volumes.test_region_pyramid` (production venv, which has h5py + nibabel) | **188 tests, OK** — confirms those 5 errors were environment-only |
| dev: `npm run typecheck` | clean |
| dev: `npm run test` (vitest) | **51 files, 266 tests, all passed** |
| prod: `manage.py test volumes annotation.tests annotation.test_api_flows core.test_admin` | **332 tests, OK** |
| prod: `npm run build:production` | built |
| prod: `manage.py check --deploy` | 2 pre-existing HSTS warnings, no errors |

**Note on the production test run.** A first attempt reported 33 failures and
20 errors across unrelated areas (chunk service, admin access, pyramid API,
data registration). Cause: the production `.env` sets
`MITO_SECURE_SSL_REDIRECT=1`, so every Django test-client request got a 301
instead of its expected status. Re-running with `MITO_SECURE_SSL_REDIRECT=0`
gives 332/332 OK. This is a profile artifact of testing against the production
env file, not a regression — worth knowing before anyone tests in that tree
again.

The production database role cannot `CREATE DATABASE`, so the production tree's
tests were run as `mito-production-v11` with `MITO_DB_*` overridden to the
development PostgreSQL (`mito_dev` on 127.0.0.1:5433). No production data was
touched by the test run.

No skipped tests were left behind for the removed feature; every split test was
either deleted or rewritten against whole-volume helpers.

## 5. Database state — no consolidation was needed

Checked **before** deleting any code, read-only, on both databases.

| Database | Volumes | Tasks | Volumes with >1 task | Tasks not spanning full volume |
| --- | --- | --- | --- | --- |
| `mito_production_v1_1_0` (production) | 33 | 13 | **0** | **0** |
| `mito_dev` (development) | 0 | 0 | **0** | — |

All 33 production volumes were `status='registered'`; **zero** rows carried the
legacy `status='split'`. There were no legacy multi-task volumes and no partial
z-extent tasks, so no destructive consolidation was proposed or performed, and
no user approval was required. Re-checked live after the redeploy: still 0
volumes with more than one task.

Because zero rows used it, `VolumeStatus.SPLIT` was removed from the enum. That
produces `volumes/migrations/0010_alter_volume_status.py` — a choices-only
`AlterField`: the column stays `varchar(20)` and no row is rewritten. It is
applied in production.

## 6. Risk list

- **Viewer bounds** — `z_start`/`z_end` are unchanged on the model and still
  populated by `create_whole_volume_task` as `0 .. shape_z`. Live check: task
  39 reports `z=[0,256)` against a volume with `shape_z=256`. Anything reading
  task bounds for viewer extents (editor, share links, proofreading launch)
  behaves identically.
- **Hard cases and shares** — key off task/volume identity, not z-ranges;
  untouched by this change and still covered by their existing tests.
- **`VolumeStatus.SPLIT` removal** — the only remaining risk is a database
  outside these two that still holds `status='split'`. `lifecycle_for_volume_status`
  uses `.get(status, Lifecycle.NEW)`, so such a row degrades to the `NEW`
  bucket rather than raising. Both known databases were verified to have none.
- **Django admin per-volume Tasks column** — deliberately kept. If it should go
  too, that is a one-line follow-up in `volumes/admin.py`.
- **Full dev suite in the conda env** — still cannot run `h5py`/`nibabel`
  tests. Pre-existing, unrelated, and not addressed here.

## 7. Deployment

**Production (`mito-data-studio-production-v1.1.1`, systemd
`mito-data-studio-v1.1.1.service`, gunicorn on 127.0.0.1:18191)**

1. Database backed up through the container (host `pg_dump` v12 vs server v16):
   `mito-backups/mito_production_v11-pre-split-removal-20260805-230509.dump`,
   163 KB, verified via the container's `pg_restore -l` → 34 `TABLE DATA`
   entries. (The host `pg_restore` cannot read it — same version skew
   `DEPLOYMENT.md` warns about for `pg_dump`.)
2. 23 source files copied from development, `split_volume.py` deleted,
   migration `0010` added. Verified beforehand that for **every one** of those
   files the only development↔production difference was this cleanup, so no
   unrelated WIP was synced.
3. `manage.py migrate volumes` → `0010_alter_volume_status` applied.
4. `manage.py collectstatic --noinput` → 0 changed, 154 unmodified.
5. `npm run build:production` → `index-DLiNsN_k.js`.
6. `systemctl reload mito-data-studio-v1.1.1.service` (graceful HUP).
7. Health: `/admin/login/` 200, `/` 200, served bundle hash matches the fresh
   build, `POST /api/volumes/<id>/split/` → 404. Public
   `https://mito-data-studio.seg.bio/` → 200 serving the same bundle, and its
   `/split/` → 404. `grep -ci traceback logs/error.log` → **0**; the only new
   log line is the expected `Not Found: /api/volumes/1/split/` from the probe.
   Both prod units still `active`.
8. Live read-only smoke against production data: Data tab
   (`GET /api/projects/24/volumes/`) 200 with 3 rows and **no** `task_count`
   key in the payload; Assign tab (`GET /api/projects/24/tasks/`) 200 with 3
   rows; task 39 detail 200 with `z=[0,256)`; volume 29 detail 200,
   `status=registered`.

`.env` was never read, printed, or modified; no feature flags changed.

**Development (`mito-data-studio`)**

- Docker image rebuilt from `Dockerfile` (`mito-data-studio:local`, core deps).
  The frontend compiles inside the image; its bundle has zero split residue,
  `split_volume.py` is absent from the image, and migration `0010` is present.
- Container smoke-run on a throwaway port (127.0.0.1:18299) against a scratch
  SQLite database: entrypoint applied all migrations including `0010`,
  collected static, gunicorn booted, Docker health check reported **healthy**,
  `/admin/login/` 200, `/` 200, `/healthz` 200, `POST .../split/` 404. Removed
  afterwards.
- **`docker-compose.dev.yml` was deliberately not recreated.** That file stands
  up only `mito-dev-postgres`, and that one container hosts *both* `mito_dev`
  **and** the live `mito_production_v1_1_0` database. Recreating it would have
  taken production down. It was left running (healthy, 9 days up) and verified
  untouched afterwards.
- The full `docker-compose.yml` stack was likewise not started: it requires a
  `.env.docker` that does not exist on this host and would stand up a second
  PostgreSQL plus a new app on port 8000 — a new deployment rather than a
  rebuild, and outside the "do not disturb unrelated live deployments" boundary.

## Files changed

**Backend (both trees):** `volumes/services.py`, `volumes/serializers.py`,
`volumes/api.py`, `volumes/admin.py`, `config/urls.py`, `config/settings.py`,
`core/choices.py`, `core/lifecycle.py`, `volumes/tests.py`,
`annotation/tests.py`, `annotation/test_api_flows.py`, `core/test_admin.py`;
added `volumes/migrations/0010_alter_volume_status.py`; deleted
`volumes/management/commands/split_volume.py`.

**Frontend (both trees):** `api/volumes.ts`, `pages/VolumeDetailPage.tsx`,
`pages/ProjectDetailPage.tsx`, `pages/LoginPage.tsx`,
`components/DatasetsCard.tsx`, `components/DatasetsCard.test.tsx`,
`components/VolumeMeta.tsx`, `types/volume.ts`, `styles.css`.

**Docs:** `.env.example`,
`docs/webknossos-transformation/13-mito-data-studio-workflow-audit.md` (both
trees); development-only `progress/`: `development.md`, `api.md`, `codemap.md`,
`admin.md`, `backend/volumes/MODULE.md`, `backend/core/MODULE.md`,
`backend/projects/MODULE.md`, `frontend/api/MODULE.md`,
`frontend/pages/MODULE.md`. (Production's `progress/` is only a `README.md`, so
there was nothing to align there.)
