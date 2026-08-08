# `frontend/src/pages/` — one file per route

Each page is a route target from `../routes/AppRoutes.tsx`. Pages fetch
their own data via `useAsync` + the `../api/` modules — there's no
shared page-level data-loading abstraction beyond that hook.

## Dashboards (role home pages)

- **`ManagerDashboard.tsx`** — lists projects + submissions awaiting
  review (`listSubmissions("submitted")`), a total-volumes count, and a
  pending-review count. Links out to `/projects`, submission review, etc.
- **`RequesterDashboard.tsx`** — the requester's own projects, filterable
  by lifecycle bucket via `LifecycleTabs` (`../features/MODULE.md`) and
  `getLifecycleCounts` (`../features/lifecycle/api.ts`).
- **`AnnotatorDashboard.tsx`** — "To annotate" (`listMyTasks` — anything
  still in their hands, **including rejected tasks**, which are work to redo
  rather than history) and "Awaiting review & approved"
  (`listMyCompletedTasks`), each rendered via `TaskTable`
  (`../components/MODULE.md`). The page itself is thin — all the
  Annotate/View routing logic lives in `TaskTable`.

## Project / dataset / volume management

- **`ProjectListPage.tsx`** — manager-only project list + "New project"
  link.
- **`NewProjectPage.tsx`** — step 1 of starting new work: create the
  `Project` (title, annotation target/type, workflow type, deadline).
  Deliberately separate from data registration — see the file's own
  docstring: "data is registered *into* a project," so the project is
  described on its own terms first, then step 2 (register data) follows.
- **`RegisterDataPage.tsx`** (780 lines — the largest page) — step 2:
  scan an HPC directory (`scanDataSources`), review/adjust the detected
  image/mask pairs, then `registerData`. Handles the manifest/heuristic
  pairing UX described in `backend/volumes/MODULE.md`.
- **`ProjectDetailPage.tsx`** — a project's datasets (`DatasetsCard`) and
  volumes, manager review toggle, delete-with-dependents
  (`projectDependents`/`deleteProjectForce`), inline editing
  (`ProjectEditForm`), `AssignmentPlanEditor` for bulk task assignment, and a
  **Hard cases** section (`ProjectHardCases` → `listHardCases({project})`,
  rendered with the shared `HardCaseList`) — the project page is just a
  pre-filtered view of the `/hard-cases` inbox.
- **`VolumeDetailPage.tsx`** — one volume's metadata, its single
  whole-volume task (`listProjectTasks` filtered), editing
  (`updateVolume`/`editVolume`), delete-with-dependents. Each
  `TaskSection` reads `can_submit`/`can_annotate`/`annotation_locked` off the
  task, and gives managers a **Close/Reopen for annotation** button
  (`setTaskAnnotationLock`).

## Task lifecycle

- **`TaskDetailPage.tsx`** — a task's full metadata table (project,
  dataset, volume, frame range, shape, voxel size, priority, difficulty,
  paths, deadline, instructions), `MetadataCard` for the dataset's
  biomedical metadata, `ProofreadingLaunch` (only for the owner/manager), a
  **Latest review** card (`last_decision*`, plus whether they may keep
  working), and a "Submit completed label" link gated on the server's
  `t.can_submit`. **Do not re-derive that from `status`** — the old
  `status in [assigned, in_progress, revision_requested]` check is exactly
  what made Submit vanish after the first submit.
- **`SubmitTaskPage.tsx`** — upload a label file for a task
  (`FileUpload` + `submitTask`).
- **`ReviewSubmissionPage.tsx`** — manager approve/reject/request-revision
  on a submission, with a comments field and an **"Allow further annotation
  after approval"** switch (default off — approve means done; on leaves the
  task open for another round). Shows which round this is
  (`task_detail.submission_count`) and the previous decision, with a note that
  only the latest submission is kept. When `submission.source ===
  "inapp"` (no uploaded file to show), renders an "Open annotation editor"
  link (`/editor/tasks/<task_id>`) instead of the label-file row, so the
  manager can actually inspect the painted labels before deciding —
  approving one promotes the working copy to the volume's official label
  (`backend/annotation/services.approve_submission`).

## Auth

- **`LoginPage.tsx`** — two login tabs (requester vs annotator, see
  `LoginPortal` in `api/auth.ts`); in dev builds only
  (`import.meta.env.DEV`) shows the standard seeded dev accounts
  (`manager`/`alice`/…/`requester1`/`requester2`, password `demo12345` —
  clicking a chip also selects the matching login tab) and a "Reset dev data"
  button (`resetDevData`) — both stripped from production builds.
- **`RegisterPage.tsx`** — self-service signup, role choice limited to
  `annotator`/`requester` (no public manager signup).

## `ViewerPage.tsx` — read this one carefully if touching the editor

Both routes it backs (`/viewer/*`, `/editor/*`) use `RequireAuth fullBleed`
(`../routes/MODULE.md`) — **global navbar stays**, but the centered
`.container` is skipped. Each component builds an `.editor-shell` that fills
`.full-bleed-main` under the navbar (flex column, `min-height: 0`) with a
slim `.editor-topbar` and an `.editor-body` the viewer/canvas fills.

**Navbar owns leaving** (brand / left nav → home, ← Back → task or volume).
**Topbar owns task work only** — do not put Done / Home / Task details here
(that was tried and felt redundant; cleaned up).

Exports two page components:
- **`VolumeViewerPage`** — always read-only (`SliceViewer`); topbar is
  title only.
- **`TaskViewerPage({editable})`** — `mayOpenEditor = isManager ||
  task.assigned_to === user?.id` decides whether the Annotate/View switch
  appears; what the canvas may actually *do* comes from the API
  (`task.can_annotate`). Topbar actions:
  - **Annotate** / **View only** — mode switch to the other route
  - **Submit for review** / **Submit again** — shown whenever the server says
    `task.can_submit`. There is **no client-side status list**; the button
    survives previous submits and rejects and only disappears once a manager
    approves-and-locks, where a `🔒 Approved — closed for further annotation`
    note takes its place. Calls `submitInappTask` and **stays on the page**,
    swapping in the refreshed task from the submission response
    (`submission.task_detail`) rather than reloading — a reload would unmount
    the canvas and throw away the annotator's in-memory slice history. This is
    the *only* in-app formal submit path.

  `editable` for the canvas is `task.can_annotate` (edit access **and** not
  locked), so a locked task opens View-only instead of offering paint tools
  whose writes would 403.

## People — `PeoplePage.tsx` / `PersonPage.tsx`

`/people` is one section for every role: an editable profile card
(`updateMyProfile` → `display_name` / `institution_name` / `contact_note`,
then `useAuth().refresh()` so the navbar catches up) plus role-specific
panels rendered from whichever lists `getPeopleOverview()` returned non-empty.
No role branching beyond picking which panels to show — the backend
(`backend/accounts/MODULE.md`) already scoped everything.

`ProjectRef` (exported from `PeoplePage`, reused by `PersonPage`) links a
project name **only for managers and requesters** — annotators can't open
`/projects/:id` (`../routes/MODULE.md`), so for them it renders as plain text
rather than a link that would bounce them home.

`/people/:username` is a read-only card showing nothing the overview doesn't
already show; it exists so a person is linkable.

## Hard Cases — `HardCasesPage.tsx` / `HardCaseDetailPage.tsx`

- **`HardCasesPage`** (`/hard-cases`) — the inbox: every case the signed-in
  user may see across all their projects, newest first (earlier receipts
  further down, like email). Open cases up top; taken-down ones behind a
  toggle rather than gone, because the record of what the team already worked
  through is the point. Body is the shared `HardCaseList`
  (`../components/MODULE.md`).
- **`HardCaseDetailPage`** (`/hard-cases/:id`, `fullBleed`) — one case on the
  **same `AnnotationCanvas`** as task View/Annotate and the public share page.
  `editable={hardCase.can_annotate}` comes straight from the API (creator or
  manager, *and* still able to edit the underlying task), so the canvas can
  never offer a tool whose write would 403. Everyone else in the audience gets
  the same canvas View-only, soloed on the flagged label
  (`initialActiveId`/`initialSoloId`). Reads and writes both use the ordinary
  authed task endpoints — the public token path is only for people without an
  account. Creator/manager also get **Take down / Reopen** and **Revoke /
  Restore link** (the latter kills only the public token).

## `HardCaseSharePage.tsx` — public read-only "hard case" viewer

Route `/share/hard-case/:token`, registered **outside** `RequireAuth` (no
account, no app navbar). Fetches `getPublicHardCaseMeta(token)` for the
task/volume identity + shared `label_id`, then mounts the shared
`AnnotationCanvas` with `editable={false}`, `api={publicHardCaseApi(token)}`
(token-backed public endpoints), and `initialActiveId`/`initialSoloId` =
the shared label so the recipient lands soloed on it (canvas + 3D — the pin
set is seeded from `initialSoloId`). They can reveal other labels via the
Labels panel. See `../features/MODULE.md`'s `AnnotationCanvas`
read-API-injection note, `progress/history/02-share-hard-case.md`, and
`progress/history/03-fix-hard-case-share-view.md`.

**Height comes from `ViewerShell standalone`** (`../components/MODULE.md`),
the same shell `ViewerPage` uses — not hand-rolled markup. This page has no
`Layout fullBleed` above it, so it must supply the viewport itself; when it
didn't, `.editor-shell`'s `height: 100%` collapsed and the page rendered as
an infinitely tall black scroll.

`initialActiveId` also **suppresses the "seed Active = next new id" effect**
in `AnnotationCanvas` (which otherwise overwrote the shared label with
`max+1` the moment `label-state/` resolved) and is passed to `LabelsPanel` as
`focusId`, which scrolls that row into view once.

## Gotchas

- **Submit/paint gating is API-driven now.** Read `can_submit` /
  `can_annotate` / `annotation_locked` off the task
  (`backend/annotation/MODULE.md`); do not re-derive them from `status`. The
  remaining `assigned_to === user?.id` / `isManager` checks decide only
  *which controls to show* (e.g. whether an Annotate link appears at all),
  and are ANDed with the server's answer — grep for both when changing who
  may edit a task.
- `RegisterDataPage.tsx` is the single largest page (780 lines) — if
  you're adding a new data-source format or pairing heuristic, expect to
  touch both this file and `backend/volumes/services.py`'s pairing logic
  together.
- Do not reintroduce Done · My Tasks / Task details / navbar Home next to
  Submit — leave stays in the navbar; Submit is the finish-work action.
