# Mito Data Studio — project overview

A web app for managing mitochondria (EM) annotation work end to end: register
image volumes → split into tasks → assign to annotators → annotate (in-browser
or externally) → submit → manager review → track progress. Annotation work is
unpaid; there is no payment/billing anywhere in the system.

Read this file first. Then jump to the module doc for whatever you're
touching — `backend/<app>/MODULE.md` or `frontend/<dir>/MODULE.md`. This file and the module docs are the current-state reference;
[`history/`](history/) is a running session log (recently reset to zero).

## Tech stack

- **Backend**: Django 5 + Django REST Framework, SQLite (dev), token auth.
- **Frontend**: React 18 + TypeScript + Vite, React Router, no UI framework
  (hand-rolled CSS in `styles.css`) and no state library (plain `useState` +
  a small `useAsync` hook).
- **Storage**: a flat filesystem root (`MITO_DATA_ROOT`) holding image/label
  volumes (TIFF/NIfTI/etc.) and submission files. The database stores paths
  relative to this root (or Django `FileField`s under the same root), never
  the large image data itself.
- **Vendored models**: `vendor/efficient_sam/` + `vendor/sam2/` — EfficientSAM
  vits ONNX and SAM2 hiera_large (tracked via Git LFS). See root `README.md`.
- **Dev entrypoint**: `./dev-setup.sh` (prepare) + `./dev-launch.sh` (run).
  Git hygiene: `./dev-setup.sh --check-git`. See "Where things run" below.

## The three roles

Everything in this app branches on one of three roles (`accounts.roles`):

| Role | Internal value | Can do |
|---|---|---|
| **Manager** | `manager` | Everything. Runs the full workflow through the Django Admin at `/admin/` (see `core/admin_site.py`) — not the React app. Reviews projects, splits volumes into tasks, assigns/auto-assigns, reviews submissions. |
| **Institution** (aka Requester) | `requester` (legacy: `client`) | Creates projects, registers datasets/volumes into them, watches progress. Cannot annotate or assign. Displayed as "Institution" in the UI — see `core/labels.py` / `frontend/src/labels.ts` for the internal-value → display-label mapping. |
| **Annotator** | `annotator` | Works the tasks assigned to them: opens the in-app editor, paints labels, submits. |

A Django superuser is always treated as a manager (`accounts/roles.py:get_role`).
The React SPA is the annotator/requester-facing surface; the manager's
*primary* daily-driver interface is the Django Admin, not React (the React
app also has manager views, but admin is where volume-splitting and
bulk-assignment live).

## Data model hierarchy

```
Project (accounts.Institution owns it via created_by)
  └─ Dataset (a registration batch: "this dataset came from this directory")
       └─ Volume (one image/label pair — a "chunk" or a whole source volume)
            └─ AnnotationTask (a z-range of one volume, assigned to one annotator)
                 ├─ AnnotationSubmission (the *latest* submission — see below)
                 │    └─ ReviewRecord (a manager's approve/reject/revise decision)
                 └─ HardCase (a label flagged for the whole project to look at)
```

Two deliberate non-obvious edges there:

* **`AnnotationSubmission` is latest-only.** Re-submitting deletes the
  previous row and any file it owned; `AnnotationTask.submission_count`
  records how many rounds happened. `ReviewRecord` therefore hangs off the
  *task* (`submission` is `SET_NULL`) so the decision log outlives the
  pruning.
* **`HardCase` is project-scoped**, not task-private: everyone on the project
  can see it. It keeps a public token as an optional extra for pasting
  outside the app.

`ProcessingJob` (in the `processing` app) is a fifth, orthogonal model: a
queued unit of async work (ingest, predict, generate tasks, ...) that can
optionally link to a Project/Volume/Task. It exists so nothing heavy ever
runs inside an HTTP request.

Full field-level detail is in each app's `MODULE.md`
([`backend/projects/`](backend/projects/MODULE.md),
[`backend/volumes/`](backend/volumes/MODULE.md),
[`backend/annotation/`](backend/annotation/MODULE.md),
[`backend/processing/`](backend/processing/MODULE.md)).

## The "lifecycle" abstraction

The product exposes three high-level buckets over the real, granular domain
statuses: **New → To Proofread → Done**. Rather than scattering
`if status in (...)` everywhere, every caller (admin dashboard, React
dashboards, API) classifies through one module: `core/lifecycle.py`. See
[`backend/core/MODULE.md`](backend/core/MODULE.md) for the exact mapping.

## The provider pattern (how this app stays swappable)

Several integrations are deliberately behind a small `interfaces.py` +
`registry.py` + `adapters/` pattern, selected by a Django setting, so the
domain service layer never imports a concrete adapter directly:

| Concern | Setting | Adapters | Lives in |
|---|---|---|---|
| Proofreading (open a task to annotate) | `MITO_PROOFREADING_PROVIDER` | `inapp` (default), `external_tool`, `neuroglancer`, `placeholder` | `annotation/proofreading/` |
| Visualization (view a volume/task) | `MITO_VISUALIZATION_PROVIDER` | `inapp` (default), `neuroglancer`, `placeholder` | `annotation/visualization/` |
| SAM2 fork-aware tracking | `MITO_TRACKING_PROVIDER` | `local` (CPU stand-in, default), `sam2` (real GPU model) | `annotation/tracking/` |
| Quality control | `MITO_QC_PROVIDER` | `basic` (default) | `annotation/quality_control/` |
| Publishing | `MITO_PUBLISHING_PROVIDER` | `placeholder` (default) | `annotation/publishing/` |
| Processing/HPC execution | `MITO_PROCESSING_BACKEND` | `local`, `slurm` | `processing/` |

Adding a new backend for any of these means writing one adapter class and
registering it — no changes to views, services, or the frontend. See
[`backend/annotation/MODULE.md`](backend/annotation/MODULE.md) for the
provider interfaces in detail (proofreading/visualization/tracking all live
under the `annotation` app).

## Request flow, end to end

1. **Requester** registers data: scans an HPC directory
   (`POST /api/hpc/scan/`), then registers it into a project as one or more
   `Volume` pairs (`POST /api/register-data/`) — see
   [`backend/volumes/MODULE.md`](backend/volumes/MODULE.md).
2. **Manager** reviews the project (`manager_reviewed=True`), then splits a
   volume into z-range `AnnotationTask`s (`POST /api/volumes/<id>/split/`)
   and assigns them (manually or via the rule-based auto-assigner in
   `annotation/services.py`).
3. **Annotator** opens `/editor/tasks/:id` in the React app. This mounts
   `AnnotationCanvas` (`frontend/src/features/viewer/`), which streams image
   slices and reads/writes label instance-ids through the slice-IO backend
   (`annotation/visualization/slice_io.py`) — see
   [`backend/annotation/MODULE.md`](backend/annotation/MODULE.md) for exactly
   how that streaming/caching/memmap layer works, and
   [`frontend/features/MODULE.md`](frontend/features/MODULE.md) for the
   editor itself.
4. Either the in-app editor auto-saves continuously (every stroke commits),
   or the annotator works externally and uploads a label file
   (`POST /api/tasks/<id>/submit/`, `AnnotationSubmission`). Submitting is
   **repeatable**: each submit replaces the previous one, and the button stays
   available until a manager approves-and-locks the task
   (`annotation.services.can_submit_task`).
5. **Manager** reviews the submission (`POST /api/submissions/<id>/review/`)
   — approve / reject / request revision, plus an **"allow further
   annotation"** switch on approve that decides whether the annotator may
   keep working (off = the task locks; paint and submit both 403). Reject and
   revision hand the task back unlocked and promote nothing. Approval and
   task/volume/project status transitions all go through
   `annotation/services.py`.
6. Alongside the task flow, anyone on a project can raise a **hard case** from
   the editor ("Record hard case") — a flagged label the whole project can
   open at `/hard-cases/:id` on the same canvas, View-only unless they are its
   creator or a manager. **People** (`/people`) shows who is on which project,
   derived from that same shared-project relation. See
   [`backend/accounts/MODULE.md`](backend/accounts/MODULE.md) and
   `progress/history/05-submit-people-hardcases.md`.

## Auth

Token-based (`rest_framework.authtoken`), not session/cookie — the SPA
stores the token in `localStorage` and sends `Authorization: Token <token>`
on every request (`frontend/src/api/client.ts`). This is why the slice-image
`<img>` tags can't use a plain `src=` URL (no way to attach a header) and
instead fetch-and-blob (`frontend/src/api/viewer.ts:fetchObjectUrl`).

## Where things run

Two scripts at the repo root (named with a shared `dev-` prefix so they sort
next to each other in a file browser), split so the everyday loop is fast —
rather than one monolithic script that redid dependency/migration checks on
every single launch. Deliberately **kept basic**: tool checks, start the two
servers, clean shutdown on Ctrl+C. No wrapper/common-file third script.

This basic-ness is itself the result of a long debugging round
that added, then **removed again**, several defensive features — conda-env
auto-detection via `conda env list`, `curl`-based readiness polling before
printing "running", `$DISPLAY`-aware branching in the browser-open step.
None of it was wrong, but none of it addressed the actual problem either:
the user's Claude Code session and their actual interactive terminal turned
out to be on **two different machines** (`a002.m31.bc.edu` vs a SLURM
compute node, `g011`) for most of that debugging — every verification "passed"
against the wrong host. No amount of script cleverness can fix a
you're-testing-the-wrong-machine problem, and the extra logic just made the
scripts harder to read for no real benefit once that was understood. Lesson
for next time: if "it works when I test it but never for the user" persists
across multiple fixes, check `hostname` on both sides *early*, before adding
more defensive code.

- **`./dev-setup.sh`** — checks required tools and imports, verifies the
  vendor weights are real files rather than Git LFS pointers, creates `.env`
  on first run (and ensures `MITO_DATA_ROOT` exists and is writable), installs
  frontend deps only if `package.json`/`package-lock.json` changed
  (hash-cached in `.dev-cache/`), runs Django `check` + `migrate`. Warns
  (doesn't fail, doesn't auto-detect/route around) if the conda env isn't
  active, if CUDA is unavailable to torch, or if `.env` still has the viewer
  providers set to `placeholder` — run `conda activate mito-data-studio`
  yourself first. Run this once, or after pulling changes that touch
  dependencies/migrations. `--smoke` additionally proves the install works on
  this machine (loads onnxruntime + a real vendor weight, imports torch,
  builds the frontend); `--check-git` is the "nothing private got tracked"
  gate.
- **`./dev-launch.sh`** — starts Django (`manage.py runserver`, `:8000`) and
  Vite (`:5173`) together, each in its own process group so one Ctrl+C tears
  down both cleanly (Vite's autoreload children included) with no orphans.
  Fixed 2-second sleep, then a plain "did the process crash" check before
  printing "running" — not readiness-polled; if a slow cold start makes this
  print before the server is truly ready, that's expected, wait a couple
  more seconds. This is what you run day to day. Backend:
  `http://127.0.0.1:8000`. Frontend: `http://127.0.0.1:5173` (proxies `/api`
  to Django). `VITE_HOST=0.0.0.0 DJANGO_HOST=0.0.0.0` binds to all
  interfaces instead of just loopback, for reaching the app via a host's
  real network IP directly — useful on HPC when SSH/VS Code port forwarding
  isn't cooperating (see the history file above); this exposes the dev
  server beyond localhost, so don't leave it running unattended like that.
  `NO_BROWSER=1` skips the (best-effort, often silently ineffective on a
  remote/headless host — that's normal, not a bug) `xdg-open`/`open` attempt.
  On a SLURM compute node reached via `srun`/`salloc` from a login node,
  also auto-bridges to the login node over a reverse SSH tunnel, with a real
  end-to-end reachability check before claiming success (not just "the SSH
  process didn't die"). Login-node detection tries `LOGIN_NODE=<host>` (
  explicit override) → `$SLURM_SUBMIT_HOST` → `scontrol show job
  $SLURM_JOB_ID` → `AllocNode` (the reliable one — `$SLURM_SUBMIT_HOST` is
  confirmed **empty** on this cluster for real interactive sessions, don't
  trust it alone). This exists because VS Code/Cursor Remote-SSH's port
  forwarding only ever sees the machine its connection actually landed on
  (the login node), never a compute node one hop further in — and for the
  actual end-to-end verification (real SLURM allocation, real compute node,
  confirmed reachable via `curl` from an independent shell on the login
  node) backing this specific mechanism.
- Manager Admin: `http://127.0.0.1:8000/admin/` (or via the React app's nav
  for managers — but admin is the actual daily-driver for splitting/assigning).
- No production deployment is configured in this repo (no gunicorn/nginx
  config checked in); both scripts are dev-only.

## Topic docs

Deeper dives that don't map one-to-one onto a single module:
[`architecture.md`](architecture.md) (service-layer rule, backend apps
table, data model, a concrete request walkthrough, the `ProcessingJob`
dispatcher — this file's own faster-orientation companion),
[`codemap.md`](codemap.md) ("I want to change X, where do I go?" lookup —
the fastest path when you already know the feature and just need the
file), [`development.md`](development.md) (run/configure/seed/test),
[`admin.md`](admin.md) (Manager Admin), [`api.md`](api.md) (REST reference).
These used to live in a separate top-level `docs/` folder, since folded
in here; see [`README.md`](README.md).

## Module doc index

**Backend** (`backend/<app>/`, one Django app each):
- [`accounts/`](backend/accounts/MODULE.md) — users, roles, institutions, auth endpoints.
- [`core/`](backend/core/MODULE.md) — cross-cutting: choices/enums, lifecycle, permissions, storage, the Manager Admin site, shared utils.
- [`projects/`](backend/projects/MODULE.md) — Project + Dataset models and services.
- [`volumes/`](backend/volumes/MODULE.md) — Volume model, HPC data registration/scanning, volume→task splitting.
- [`annotation/`](backend/annotation/MODULE.md) — the largest app: tasks, submissions, review, the slice-streaming/label-editing backend, SAM2 fork-aware tracking, and all the provider interfaces.
- [`processing/`](backend/processing/MODULE.md) — ProcessingJob queue + local/SLURM execution backends.
- [`config/`](backend/config/MODULE.md) — Django settings, URL routing, the settings-driven provider switches.

**Frontend** (`frontend/src/<dir>/`):
- [`api/`](frontend/api/MODULE.md) — the thin fetch wrapper + one module per backend resource; also covers `auth/AuthContext.tsx`, `hooks/useAsync.ts`, and `types/`.
- [`routes/`](frontend/routes/MODULE.md) — `AppRoutes.tsx` (role-gated routing) + back-navigation logic.
- [`pages/`](frontend/pages/MODULE.md) — one file per route: dashboards, project/volume detail, register data, submit/review.
- [`components/`](frontend/components/MODULE.md) — shared UI pieces (tables, cards, forms, delete-with-confirm) used across pages.
- [`features/`](frontend/features/MODULE.md) — the in-app annotation editor (`AnnotationCanvas`/`SliceViewer`), the proofreading-provider launcher, and the lifecycle tab bar.
