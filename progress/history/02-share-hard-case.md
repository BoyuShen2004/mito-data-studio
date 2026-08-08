# NEXT — Share as a hard case (public read-only link)

> **Status: implemented (2026-07-26).** See "Implementation notes" at the
> bottom.  
> User ask (2026-07-26). Screenshot of Annotate UI is the UX reference
> (Save is the blue button top-right of the tool chrome; Active id is the
> instance to share; Labels list ○/◉ is solo).

When done: mark Status implemented, update module docs
(`frontend/features/MODULE.md`, `progress/api.md`, routes MODULE), summarize
at the bottom. **Do not push** unless the user asks.

---

## User ask

1. Next to the blue **Save** button, add a **larger** button roughly named
   **Share as a hard case** (wording can be polished; keep the intent).
2. Clicking it creates a **shareable link**. Anyone with the link can open it
   **without an account**, in **View-only** mode (look, no edit / no Save /
   no paint / no Track mutations).
3. The shared “case” is the **Active** label id at share time
   (`activeId` in Annotate chrome — the number next to Active / New).
4. Recipients open the link with **default visibility = only that shared
   label** — same effect as Labels list **solo** (○ → ◉ for that id). Canvas
   and **3D Labels** both respect that default.
5. Recipients **may** use the Labels panel (and related visibility / 3D pin
   controls) to reveal other labels afterward — default-only solo, not a
   hard lock.

---

## Product rules

| | |
| --- | --- |
| Auth | Public: no login required on the share URL |
| Mode | View only — reuse View/`editable={false}` paths; strip annotate tools |
| Subject | Snapshot of `activeId` (+ task/volume identity) at share time |
| Default UI | `soloId = sharedLabelId` (and pin that id in 3D if needed so 3D isn’t empty) |
| Escape hatch | User can un-solo / show other labels via Labels UI |
| Mutations | Reject any write API from the share session (paint, save, track, split, …) |

---

## Suggested design (implement unless you find a cleaner existing pattern)

### Backend

- New model e.g. `HardCaseShare` (or `SharedHardCase`):
  - `token` — unguessable URL-safe secret (primary lookup key)
  - `task` FK (or volume + enough to open the same viewer)
  - `label_id` — the Active instance shared
  - `created_by`, `created_at`
  - optional: `expires_at`, `revoked` (nice-to-have; not required for v1)
- `POST /api/tasks/<id>/hard-case-shares/` (auth required — annotator/manager
  who can open Annotate)
  - body: `{ "label_id": <activeId> }` (or derive from server state if you
    already trust client active id)
  - response: `{ "url": "<absolute or path>", "token": "...", "label_id": N }`
- Public read endpoints under something like
  `/api/public/hard-cases/<token>/…` with **`AllowAny`**:
  - meta: task/volume shape, shared `label_id`, title bits
  - slice image / label-ids / labels-summary / labels-3d **read-only**
    clones of existing viewer APIs, scoped to that task/volume
  - **Do not** expose write endpoints publicly; if reused views must stay
    auth-gated for writes
- Copy link to clipboard on the client after create; show a short toast /
  modal with the URL

### Frontend

- Button in `AnnotateToolChrome` beside Save (bigger than Undo/Redo; secondary
  or distinct from primary Save blue — readable, not tiny).
- Route e.g. `/share/hard-case/:token` (or `/h/:token`) registered **outside**
  `RequireAuth`.
- Page mounts the same `AnnotationCanvas` (or task View shell) with:
  - `editable={false}`
  - initial `soloId = sharedLabelId` (and ensure Active/highlight matches)
  - API base = public hard-case endpoints (token in path/header), not the
    authed task APIs
- Hide annotate chrome (tools, Save, Share, Track mutate, Submit, …). Keep
  z-nav, zoom, brightness/contrast, label opacity, Labels list visibility /
  solo / 3D pins as needed for “look around”.

### Security / data

- Token entropy high enough to be unguessable (e.g. 32+ bytes urlsafe).
- Share exposes **read** of that volume’s image + working labels for the
  linked task — document that in UI copy (“anyone with the link can view”).
- Prefer not listing all shares publicly; only token URL works.

---

## Key files (start here)

| Area | Paths |
| --- | --- |
| Save / Active chrome | `frontend/src/features/viewer/annotate/AnnotateToolChrome.tsx` |
| Canvas / solo / 3D | `frontend/src/features/viewer/AnnotationCanvas.tsx`, `LabelsPanel.tsx` (○/◉), `Labels3DPanel.tsx` |
| Routes / auth | `frontend/src/routes/…`, `ViewerPage.tsx` |
| Viewer API | `frontend/src/api/viewer.ts` |
| Task APIs | `backend/annotation/api.py`, `services.py` |
| Auth permissions | `backend/accounts/…`, DRF permission classes |

---

## Acceptance checklist

- [x] Annotate: large **Share as a hard case** next to Save.
- [x] Creates link; copy-able; opens without login.
- [x] Opened page is View-only (no paint/save/track writes).
- [x] Default: only Active-at-share-time label visible (solo ○/◉ semantics) on
      canvas **and** 3D.
- [x] Recipient can turn on other labels via Labels controls.
- [x] Authed write APIs still require login; public token cannot mutate.
- [x] Tests for create-share (auth) + public GET (anon) + write rejected.
- [x] Docs updated (`api.md` / features MODULE + routes/pages MODULE).

---

## Out of scope (v1)

- Editing through the share link  
- Sharing multiple labels at once (unless trivial)  
- Email / notification delivery  
- Password-protected shares  

---

## Claude prompt (copy-paste)

```text
Read progress/ as source of truth. NEXT brief is:
progress/history/02-share-hard-case.md

Implement “Share as a hard case”: button beside Save; public view-only link
for the current Active label; default solo that label (canvas + 3D); recipient
can still show other labels via Labels UI; no account required; no edits.
Follow the brief’s API/UI sketch. Update docs. Do not push.
```

---

## Implementation notes (fill in when done)

_Status: implemented (2026-07-26)._

### Backend

- **`HardCaseShare` model** (`annotation/models.py`) + migration
  `0005_hardcaseshare`: `token` (unguessable `secrets.token_urlsafe(32)`,
  unique/indexed, default), `task` FK, `label_id`, `created_by`,
  `created_at`, `revoked`. `.path` → `/share/hard-case/<token>`.
- **Services** (`annotation/services.py`): `create_hard_case_share(task,
  user, label_id)` and `get_hard_case_share(token)` (live, non-revoked only).
- **API** (`annotation/api.py`):
  - `HardCaseShareCreateView` — `POST /api/tasks/<id>/hard-case-shares/`,
    `IsAuthenticated` + `can_edit_task` (manager or assigned annotator).
    Body `{label_id}` → `{token, label_id, url}`.
  - `_PublicHardCaseView` base (`AllowAny`, `authentication_classes = []`) +
    `PublicHardCaseMetaView/SliceView/LabelStateView/LabelIdsView/
    LabelsSummaryView/Labels3DView` under
    `/api/public/hard-cases/<token>/…` — read-only clones of the viewer GETs,
    scoped to the share's task/volume. **No public write path.**
- URLs wired in `config/urls.py`.

### Frontend

- **`api/viewer.ts`**: a `ViewerReadApi` adapter interface + `authedViewerApi`
  (default) + `publicHardCaseApi(token)` (token endpoints, no auth header),
  `createHardCaseShare`, `getPublicHardCaseMeta`, and a shared `decodeLabels3D`.
- **`AnnotationCanvas.tsx`**: new `api` prop (defaults to `authedViewerApi`)
  routing its 6 read calls; `initialActiveId`/`initialSoloId` props; the
  "Share as a hard case" flow (create → copy link → modal). `Labels3DPanel`
  gained an injectable `fetchLabels3D`.
- **`AnnotateToolChrome.tsx`**: emerald **"🔗 Share as a hard case"** button
  beside Save (`editable` only), with `onShare`/`sharing` props.
- **`pages/HardCaseSharePage.tsx`** + route `/share/hard-case/:token`
  **outside** `RequireAuth`/`Layout`: mounts the shared canvas view-only via
  the public api, soloed on the shared label; a read-only banner instead of
  the app navbar.
- CSS for the button, the share modal, and the public banner in `styles.css`.

### Behavior vs. the acceptance checklist

- [x] Large Share button next to Save (Annotate only).
- [x] Creates a link, copies it to the clipboard, opens without login.
- [x] Opened page is view-only — `editable={false}` mounts no tool chrome and
      every mutation path is inert; the public API has **no** write endpoint,
      and the authed write endpoints still require login (test-covered).
- [x] Default = only the Active-at-share-time label visible (solo), on canvas
      **and** 3D (`initialSoloId` + `initialActiveId` → the shared label).
- [x] Recipient can reveal other labels via the Labels panel (solo is a
      default, not a lock).
- [x] Tests: `annotation/test_tracking.py::HardCaseShareApiTests` (11) — auth
      create gating (annotator/manager 201; requester/other-annotator/anon
      403/401; bad label_id 400), anonymous public meta/slice/label-ids/
      labels-summary/labels-3d, bad + revoked token → 404, no public write
      (PUT 405) + authed write still login-gated.
- [x] Docs: `api.md`, `frontend/features|routes|pages/MODULE.md`,
      `backend/annotation/MODULE.md`, this file.

### Notes / out of scope (unchanged from the brief)

- One share = one label; no multi-label share, no expiry UI, no email, no
  password. `revoked` exists as a kill switch but there's no UI to set it yet.
- Frontend `tsc --noEmit` clean; backend `manage.py check` clean; the 11 new
  tests pass. **Not pushed.**
