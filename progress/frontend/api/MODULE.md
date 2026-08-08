# `frontend/src/api/` — backend client

The entire app's HTTP layer: one thin fetch wrapper plus one module per
backend resource. No data-fetching library (no React Query/SWR) — just
`fetch` + the `useAsync` hook (`../hooks/useAsync.ts`, documented below).

This doc also covers `auth/AuthContext.tsx`, `hooks/useAsync.ts`, and
`types/` since they're small, tightly coupled to the API layer, and don't
warrant their own module folders.

## `client.ts` — the fetch wrapper

Token-based auth, not cookies: `getToken()`/`setToken()` read/write
`localStorage["mito_token"]`, and `apiRequest` attaches
`Authorization: Token <token>` to every request. This is *why* slice images
can't be plain `<img src="/api/...">` — no way to attach a header to an
`<img>` tag — see `viewer.ts`'s `fetchObjectUrl` below.

`api` object: `get`/`post`/`postForm`/`put`/`patch`/`patchForm`/`del`. All
requests go through `apiRequest<T>(path, {method, body, isForm, signal})`,
prefixed with `/api`. JSON body by default; `isForm: true` sends `FormData`
as-is (file uploads). `ApiError` carries `status`/`data`; `extractMessage`
pulls a human-readable message from DRF's error shape (`detail`, or the
first field error). `get`/`post` both take an optional trailing
`AbortSignal` — `AnnotationCanvas`'s Point/Box/Boundary predict calls use this to drop a
superseded request when a newer click/box supersedes it.

## `viewer.ts` — slice streaming + label editor

The API surface for `SliceViewer`/`AnnotationCanvas`
(`../features/MODULE.md`). Read this alongside
`backend/annotation/MODULE.md`'s slice_io section — the two are tightly
coupled.

- `imageSlicePath(volumeId, {axis, index})` — **no `window`/`level`** in
  the URL by default; the backend normalizes against the volume-wide
  `display_range` and returns JPEG, fetched once per (axis, index) no
  matter how brightness/contrast sliders move afterward (applied via a CSS
  `filter` client-side instead).
- `labelSlicePath(volumeId, axis, index)` — the colorized read-only RGBA
  overlay (view mode only).
- `fetchObjectUrl(path)` — fetches with the auth header, returns a blob
  object URL for use as an `<img src>`. `SliceViewer`/`AnnotationCanvas`
  keep these in a bounded LRU (`BlobLRU`, 256 entries, revoking evicted
  URLs) so memory stays flat regardless of how far you scrub.
- `trackTaskFork(taskId, seeds, zRange?)` — SAM2 tracking. `SeedInput.rle`
  is **true-runs** RLE (`[start, length]` of contiguous truthy pixels) —
  different encoding from the label-ids RLE below.
- `getLabelState`/`getLabelIds`/`putLabelIds` — the raw instance-id
  read/write the editor paints against. `decodeRuns`/`encodeRuns` convert
  between a flat `Int32Array`/`Uint32Array` and row-major full-coverage RLE
  (`[[id, count], ...]`) — **this** is a different RLE format from
  `trackTaskFork`'s seed RLE; don't reuse one encoder for the other.
- `fetchLabels3DMesh(taskId, labelIds, signal?)` + `decodeLabels3DMesh` —
  the 3D Labels payload: `POST /tasks/:id/labels-3d-mesh/` returning
  marching-cubes surfaces. The decoder builds `Float32Array`/`Uint32Array`
  **views onto the response buffer** (no copy), which is only safe because
  every field in the format is 4-byte aligned — keep it that way if you
  extend the header. Vertices are `(z, y, x)` in whole-volume voxel units;
  the panel applies `voxelSize` and centring.
- `ViewerReadApi` — the injectable read surface (`getVolumeMeta`,
  `getLabelState`, `getLabelsSummary`, `getLabelIds`, `imageSlicePath`,
  `fetchLabels3DMesh`). `authedViewerApi` is the default;
  `publicHardCaseApi(token)` is the same shape backed by the public
  token endpoints, which is what lets **one** `AnnotationCanvas` serve both
  the authed viewer and the account-less share page. Add a read call here,
  not as a direct import, or the share page will 401 on it.

## Other resource modules

| File | Backend resource |
|---|---|
| `auth.ts` | `login`/`register`/`logout`/`fetchMe`. `LoginPortal` = `"requester" \| "annotator"` (which login tab). |
| `projects.ts` | `Project` CRUD, `getProjectSummary` (progress + workload). |
| `datasets.ts` | `Dataset` CRUD, `datasetDependents`/`deleteDataset`, plus (oddly located here rather than `projects.ts`) `projectDependents`/`deleteProjectForce`. |
| `volumes.ts` | `Volume` CRUD (`registerVolume`/`updateVolume` as `FormData` — file upload path), `buildVolumePyramid`. |
| `tasks.ts` | Task CRUD, assignment (`assignTasks`, preview/apply-plan, `assignTaskToAnnotator`), `listMyTasks`/`listMyCompletedTasks`, `listAnnotators`, `setTaskAnnotationLock` (manager reopen/close). |
| `submissions.ts` | `submitTask` (FormData upload), `submitInappTask`, `listSubmissions`, `reviewSubmission` (takes `allowFurtherAnnotation`). |
| `hardCases.ts` | `createHardCase`, `listHardCases({project?, volume?, status?})`, `getHardCase`, `setHardCaseStatus` (take down / reopen), `setHardCaseRevoked` (public token only). |
| `people.ts` | `getPeopleOverview` (the whole `/people` page, role-scoped), `getPerson`, `updateMyProfile`. |
| `registerData.ts` | `scanDataSources` (HPC directory scan/pairing preview), `registerData` (the actual registration call). |
| `dev.ts` | `resetDevData()` — the login page's dev-only reset button. |

## `auth/AuthContext.tsx`

React context wrapping the current user + auth actions
(`login`/`register`/`logout`/`refresh` — the last re-fetches the current
user after a profile edit so the navbar/People card catch up, and swallows
its own failure rather than logging anyone out), plus derived
`isManager`/`isRequester`/`isAnnotator` booleans (mirroring `accounts.roles` predicates — kept in
sync by hand, not generated). On mount, if a token exists in
`localStorage`, calls `fetchMe()` to hydrate the user; a failed fetch
clears the user (but doesn't proactively clear the bad token — see
Gotchas). Every page that needs the current user calls `useAuth()`.

## `hooks/useAsync.ts`

The app's entire "data fetching" abstraction: `useAsync(fn, deps)` →
`{data, loading, error, reload}`. Runs `fn()` on mount/dep-change, tracks
loading/error state, and exposes `reload()` (bumps an internal tick to
re-run). Every page/component that fetches data uses this instead of a
raw `useEffect`. `error` is stringified via `ApiError.message` when
available.

## `types/`

Plain TypeScript types mirroring the Django models/serializers, kept in
sync by hand (no codegen from the DRF serializers):
- `index.ts` — shared literal unions mirroring `TextChoices` (`Role`,
  `LabelType`, `TaskType`, `TaskStatus`, `AnnotationType`, `ProjectStatus`,
  `QCStatus`, `ReviewDecision`) + `CurrentUser`.
- `project.ts`, `volume.ts`, `task.ts`, `submission.ts`, `hardCase.ts`,
  `people.ts` — one interface (sometimes several, e.g. `task.ts` also has
  `AssignResult`/`PlanEntryTask`/`AssignmentPlanPreview`) per resource.
  `AnnotationTask` carries the **server-decided gates** `can_submit` /
  `can_annotate` / `annotation_locked` — read those rather than re-deriving
  permissions from `status` (`backend/annotation/MODULE.md`).

## Gotchas

- **Two independent RLE formats** live in `viewer.ts` — label-ids
  (full-coverage `[id, count]` runs) and tracking seeds (true-only
  `[start, length]` runs). Mixing them up produces garbage silently (both
  decode to *something*, just the wrong thing) rather than an error.
- `AuthContext` doesn't clear a stale/invalid token from `localStorage` on
  a failed `fetchMe()` — if you're debugging "why does the app think I'm
  logged out but the token is still there," that's why; the user has to
  actually log out (or the token has to naturally 401 on a later request)
  to clear it.
- `types/` has no single source of truth with the backend — if you add or
  rename a Django model field, you must update the matching TS interface
  by hand. There's no build-time check that they've drifted.
