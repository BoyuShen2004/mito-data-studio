# NEXT — Submit/approval loop + People + project Hard Cases

> **Status: implemented** (2026-07-27). See [Implementation notes](#implementation-notes).
> User ask (2026-07-27): three product slices that must land as **one coherent
> collaboration layer** — (1) a durable submit↔review loop, (2) a real People
> surface for who-works-with-whom, (3) project-scoped Hard Cases (not only a
> public anonymous link).
> Builds on today’s UI (merged volume/task page, always-visible Annotate chrome,
> existing public `HardCaseShare`) and the staging/approve path already in
> `annotation.services` (`submit_inapp_annotation` → `approve_submission` →
> `_promote_working_label_to_official`). **Do not reinvent** that promotion
> path; extend it.

When done: mark Status implemented, update module docs
(`progress/api.md`, `progress/PROJECT.md`, `progress/README.md`,
`backend/annotation/MODULE.md`, `backend/accounts/MODULE.md`,
`frontend/pages/MODULE.md`, `frontend/routes/MODULE.md`,
`frontend/features/MODULE.md`), append Implementation notes below.
**Do not push** unless the user asks. Prefer one coherent UX over three
disconnected features.

---

## Design north star (read before coding)

Think of this as a **project progress / collaboration tool**, not three
isolated widgets:

| Role | Needs |
| --- | --- |
| **Annotator** | Always able to hand work to the manager; see who else is on the project; flag hard cases for the team; keep working after reject; optionally keep working after approve if the manager allows |
| **Manager** | Always review the *latest* submission only; approve → merge into official mask; choose whether the annotator may continue; see people + workload + hard cases; take down resolved hard cases |
| **Requester (customer)** | See their projects / requested work; appear on the manager’s People view; see hard cases on their projects (view-only) |

Keep View / Annotate / hard-case View on the **same** `AnnotationCanvas`
codepath (`editable` + permission gates). Do not fork a second editor.

---

## Goals → acceptance

| # | Goal | Done when |
| --- | --- | --- |
| **A** | Submit always available until locked | Annotator still sees **Submit for review** after prior submits / rejects; only disappears (or disables with clear reason) when the manager has **approved and locked** further annotation |
| **B** | Latest submission wins | Re-submit replaces previous open submission(s) for that task; older rows + stored upload files are deleted; manager review queue shows only the latest |
| **C** | Approve merges + optional continue | Approve promotes working → official mask (existing path); manager gets a **switch** “allow further annotation”; if off, task is locked for paint/submit; if on, annotator may keep editing and submit again |
| **D** | Reject → keep proofreading | Reject / revision_requested returns the task to a submittable state; Submit stays; no merge |
| **E** | People page | Every role has a People surface: short profile; annotator sees manager(s) + peer annotators on shared projects + own assignment counts; manager sees annotators (tasks done / submissions / approve·reject) **and** customers (requesters) + their projects; seed **two** mock requesters |
| **F** | Project Hard Cases | Annotator flags Active label → confirm → optional copy link (“Copied”, **no** `!!!`); case listed for project members (requester if any, manager, project annotators); only **creator + manager** may Annotate / take down; everyone else View-only; list newest-first (email-style); tied to volume; visible under project + a Hard Cases inbox |

---

## A–D — Submit & approval loop

### Current gaps (fix these)

- Frontend gates Submit on
  `["assigned", "in_progress", "revision_requested"]`
  (`ViewerPage.tsx`, `TaskDetailPage`, volume `TaskSection`) — **Submit
  disappears** after `submitted` / `approved`.
- Backend `submit_*` creates a **new** `AnnotationSubmission` each time;
  prior submissions are kept. Manager can see history — user wants
  **latest only**, delete older to save space.
- Approve already promotes in-app working mask; there is **no**
  “allow further annotation” switch / lock.

### Product rules

1. **Submit for review never disappears** while the annotator is still
   allowed to work on the task.
2. Annotator may submit **many times** until the manager **approves and
   locks** (or the task is otherwise closed). Each new submit:
   - creates / replaces the single current submission for that task;
   - **deletes** previous submission rows for that task and any
     `label_file` / upload artifacts they owned;
   - sets task status to `submitted` (or keep a clear “awaiting review”
     state — prefer reusing `submitted`).
3. Manager review UI shows **only the latest** submission per task.
4. **Reject** (or revision requested): task returns to a state where
   Annotate + Submit work again (`revision_requested` or `in_progress` —
   pick one consistently; document it).
5. **Approve**:
   - run existing `_promote_working_label_to_official` for in-app
     submissions;
   - set task `approved` (+ `approved_at`);
   - persist manager choice **`allow_further_annotation`** (boolean
     switch on the review form, default **off** recommended so approve
     means “done” unless explicitly reopened):
     - **off** → lock: no paint / no Submit (UI disabled + API 403);
     - **on** → annotator may continue Annotate + Submit (new latest
       submission; prior deleted again). If they submit again, task
       returns to `submitted` for another review round.
6. Layout: keep **Submit for review** in the editor topbar (right of
   `Task #N`, left of project · volume) — already placed; only fix
   visibility / status gates.

### Suggested implementation

**Backend**

- Task field e.g. `annotation_locked: bool` (default `False`), set `True`
  on approve when `allow_further_annotation=False`; cleared on reject or
  when manager flips the switch later.
- `submit_inapp_annotation` / `submit_annotation`:
  - if `annotation_locked` → error;
  - before create: delete other submissions for this task (and files);
  - always allowed from statuses that mean “annotator still owns the
    work” including after previous submits if unlocked — define a single
    helper `can_submit_task(user, task)`.
- `approve_submission(..., allow_further_annotation: bool = False)`.
- Manager list/detail endpoints: expose only latest submission per task
  (or filter in serializer).

**Frontend**

- Replace `SUBMITTABLE_STATUSES` checks with API-driven
  `task.can_submit` / `task.annotation_locked` (don’t drift again).
- Review page: Approve button + switch “Allow further annotation”.
- After submit, stay able to open Annotate; Submit remains for next
  round unless locked.

**Tests**

- Multi-submit: only one `AnnotationSubmission` remains; disk artifact
  of older upload gone.
- Approve+lock → submit/paint 403; Approve+allow → submit ok.
- Reject → submit ok; mask not promoted.

---

## E — People (collaboration surface)

### Product rules

- New nav item **People** (or **Team**) for all authenticated roles —
  one app section, role-specific panels (don’t build three unrelated
  apps).
- **Profile (all users):** short editable personal info (display name,
  lab/institution, optional contact note). Reuse/extend
  `UserProfile` / `AnnotatorProfile` / `Institution` rather than a new
  parallel profile model if possible.
- **Annotator sees:**
  - their **manager(s)** (derive from projects they have tasks on, or
    explicit project membership — prefer project-centric);
  - **peer annotators** on the same project(s);
  - counts: tasks assigned / active / submitted / approved / rejected.
- **Manager sees:**
  - **annotators** (who they are; tasks assigned; submissions made;
    last decision approve/reject);
  - **customers / requesters**: who requested which projects; current
    active project(s).
- **Requester sees:** their projects + which manager owns them (light
  panel is enough for v1).

### Seed data

Extend `seed_dev` / `STANDARD_ACCOUNTS` with **two mock requesters**,
e.g. `requester1`, `requester2` (password still `demo12345`). Show them
on Login demo chips when `VITE_SHOW_DEMO_ACCOUNTS` is on. Wire them to
existing demo project(s) as owners/customers if a project exists; if
not, still create the accounts so People UI is demonstrable.

### Suggested routes

- `/people` — hub
- optional `/people/:username` — read-only card

Use existing list APIs where possible; add a small
`GET /api/people/overview/` (role-scoped) if fan-out queries get messy.

---

## F — Project Hard Cases (evolve today’s public share)

### Current state

- `HardCaseShare` + public `/share/hard-case/:token` View-only link
  (see `history/02-share-hard-case.md`, `03-fix-hard-case-share-view.md`).
- Button still reads like a public share CTA.

### Product rules

1. Primary action becomes **Record / share hard case with the project**
   (wording polish OK — drop obligatory “🔗 Share as a hard case” if a
   clearer label fits). Still requires Active label id to exist (keep
   the guard already added).
2. Flow in Annotate:
   1. Click action → **confirm** modal (“Share this label with everyone
      on this project?”).
   2. On confirm → create project hard case → show **link** + **Copy**
      (optional). After copy: button/label becomes **Copied** — **not**
      `Copied!!!`.
3. Audience: project **manager**, **all annotators with tasks on that
   project**, and **requester** of the project if any. Not the whole
   world by default.
4. Permissions:
   - **View**: all audience members (+ existing public token link if you
     keep it as an optional extra).
   - **Annotate** on the case: **only** `created_by` and **managers**.
   - **Take down** (resolve/revoke/hide from active list): **only**
     creator or manager. Others can still *see* historical/listed cases
     as View-only but cannot annotate or take them down.
5. Listing:
   - **Project page**: Hard cases for that project.
   - **Hard Cases** inbox (nav): all cases the user may see, ordered
     **newest first** (earlier receipts further down — like email).
   - Each row ties to **volume** (+ task, label id, creator, time,
     status active/resolved).
6. Opening a case uses the same canvas as today’s share View; editable
   only when permitted.

### Suggested model evolution

Prefer **extending** `HardCaseShare` (or rename to `HardCase`) with:

- `project` FK (denormalized from task for queries)
- `volume` FK
- `status`: `open` | `resolved` (take-down sets `resolved` + optional
  `resolved_by` / `resolved_at`; keep `revoked` for public token kill)
- keep `token` for optional copyable link (in-app members can also open
  via `/hard-cases/:id` without the token)

Public anonymous link may remain for external paste, but **project
membership is the source of truth** for “everyone related to this
project can see it”.

### Frontend surfaces

- Annotate chrome button + confirm + link/copy modal (Copied, no `!!!`).
- `/hard-cases` inbox; section on `/projects/:id`.
- Manager + creator: Annotate + Take down controls on the case page.

---

## Out of scope / do not

- Do not remove the official-mask promotion on approve.
- Do not build a separate annotation editor for hard cases.
- Do not keep unbounded submission history “for audit” in v1 — user
  explicitly wants latest-only + delete to save space. If you need an
  audit breadcrumb, a thin `ReviewRecord` / event log is enough; do not
  keep old mask files.
- Do not push to remote; do not force-deploy Cloudflare unless asked.
- Do not break demo login chips (`VITE_SHOW_DEMO_ACCOUNTS` / `seed_dev`).

---

## Implementation order (recommended)

1. **A–D submit/lock/latest-only** (backend + editor topbar + review UI)
2. **Seed two requesters** + minimal People API/page skeleton
3. **People panels** (annotator peers + manager customers)
4. **Hard Cases** model/API → confirm/copy UX → project + inbox lists →
   permissioned Annotate / take down
5. Docs + tests

---

## Files to touch first (orientation)

| Area | Start here |
| --- | --- |
| Submit / approve | `backend/annotation/services.py` (`submit_*`, `approve_submission`), `backend/annotation/api.py`, `frontend/src/pages/ViewerPage.tsx`, `ReviewSubmissionPage.tsx` |
| People / seed | `backend/core/dev_data.py`, `backend/accounts/`, `frontend/src/pages/*Dashboard*`, `Navbar`, `AppRoutes` |
| Hard cases | `annotation.models.HardCaseShare`, `create_hard_case_share`, `AnnotateToolChrome` / share modal, `HardCaseSharePage`, project detail |

Codemap: `progress/codemap.md`.

---

## Implementation notes

Shipped as one collaboration layer, not three widgets: **project membership**
is the single relation underneath all of it. `annotation.services.
is_project_member(user, project)` — manager, the requester who owns it, or any
annotator holding a task on it — now backs hard-case visibility, People, *and*
task/volume viewing. That's what lets a teammate open a flagged case through
the ordinary authed viewer instead of a second permission system.

### Migrations

| Migration | Contents |
| --- | --- |
| `annotation/0006_submit_lock_and_project_hard_cases.py` | `AnnotationTask.annotation_locked` / `submission_count` / `last_decision{,_at,_by,_comments}`; `ReviewRecord.task` FK + `submission` → nullable `SET_NULL` (+ backfill); **`RenameModel` `HardCaseShare` → `HardCase`** with `project`/`volume`/`status`/`resolved_by`/`resolved_at` added and backfilled |
| `accounts/0003_userprofile_contact_note_userprofile_display_name.py` | `UserProfile.display_name`, `UserProfile.contact_note` |

`HardCaseShare` is **renamed, not dropped and recreated** — existing tokens keep
resolving, which matters because those links may already be pasted somewhere.

### A–D — status machine

The one rule: **`annotation_locked` is the only gate.** There is no status
list anywhere, on either side of the wire.

| From | Event | To | `annotation_locked` |
| --- | --- | --- | --- |
| assigned / in_progress / revision_requested / rejected / submitted / approved-but-open | submit | `submitted` | unchanged (`False`) |
| submitted | approve, `allow_further_annotation=False` (default) | `approved` | **`True`** — paint + submit 403 |
| submitted | approve, `allow_further_annotation=True` | `approved` | `False` — another round allowed |
| submitted | reject | `rejected` | `False` |
| submitted | request revision | `revision_requested` | `False` |
| any | `POST /tasks/<id>/annotation-lock/` (manager) | unchanged | as sent |

- `can_submit_task` / `can_annotate_task` are serialized as `can_submit` /
  `can_annotate` on every task; the frontend reads those and never re-derives
  from `status` (the old `SUBMITTABLE_STATUSES` list is gone from
  `ViewerPage`, `TaskDetailPage`, and `VolumeDetailPage`).
- **Latest-only**: `_supersede_submissions` deletes the task's previous
  submission row *and* unlinks any uploaded `label_file` from storage
  explicitly (a queryset `.delete()` orphans files —
  `history/17-fix-dev-reset-orphaned-files.md`). In-app submissions own no
  file. `SubmissionListView` filters through `latest_submission_ids()` so a
  pre-existing DB with a history pile also shows one row per task.
- **Reject vs revision**: kept as *distinct statuses* (the manager's verdict is
  worth showing on a badge) but neither gates anything. That satisfies "pick
  one consistently" via the lock flag rather than by collapsing the two.
  Consequence: `/my-tasks/` now lists `rejected` alongside the active
  statuses — it's work to redo, not history — and
  `/my-completed-tasks/` is submitted + approved.
- The **decision log survives** the pruning: `ReviewRecord.submission` is
  `SET_NULL` with a durable `task` FK, and `_record_review` denormalizes the
  latest decision onto the task. That's what the People panels count.
- After an in-app submit the editor **stays mounted** and swaps in
  `submission.task_detail` instead of reloading — a reload would unmount
  `AnnotationCanvas` and discard the annotator's in-memory slice history.

### E — People

`GET /api/people/overview/` (role-scoped, one round trip),
`PATCH /api/people/me/`, `GET /api/people/<username>/`; routes `/people` and
`/people/:username`, plus a **People** nav item for every role.

Derivations are all projections of shared projects (`accounts/services.py`):
annotator → managers of their projects + peer annotators on them; manager →
the annotator roster (workload, submissions made, last decision) + the
requesters with the projects they registered; requester → their projects, who
runs them, and who is working on them. `project_managers()` reads
`created_by`/`reviewed_by` and falls back to "every manager" for an unreviewed
project — an empty list is a worse answer than "any of these people".

Profile lives on `UserProfile` (`display_name`, `institution_name`,
`contact_note`) rather than a parallel model; the PATCH allow-list
(`EDITABLE_PROFILE_FIELDS`) deliberately excludes role and the institution
*link*. Seed adds **`requester1`/`requester2`** (password `demo12345`) plus
`STANDARD_PROFILES` display names, and the login demo chips now switch to the
matching tab when clicked.

### F — Hard Cases

`HardCaseShare` → `HardCase`, project/volume denormalized. Routes:
`/hard-cases` (inbox, newest-first, taken-down cases behind a toggle),
`/hard-cases/:id` (the case on the **same `AnnotationCanvas`**), plus a
per-project section on `/projects/:id` sharing one `HardCaseList` component.
The Annotate button is now **"Record hard case"** with a confirm step ("Share
this label with everyone on this project?"), and the copy affordance says
**Copied**, not `Copied!!!`.

| action | manager | creator | other project member | token holder |
| --- | --- | --- | --- | --- |
| view | yes | yes | yes | yes (unless revoked) |
| annotate | yes | yes¹ | no | no |
| take down / revoke | yes | yes | no | no |

¹ `can_annotate_hard_case` also requires `can_annotate_task` on the underlying
task. A case is not a separate document — annotating one writes the task's
working copy through the ordinary editor endpoints — so a creator whose task
was reassigned or locked drops to View-only rather than getting a button that
403s.

Take down **resolves, never deletes** (`status` `open`/`resolved` +
`resolved_by`/`resolved_at`), so members keep read access to settled cases.
`revoked` stays separate and kills only the public token; in-app access is
unaffected. A **locked** task can still have cases recorded — flagging one is
not annotating it.

### Tests run

```
cd backend && python manage.py test annotation accounts core   # 227 tests
cd frontend && npx tsc --noEmit && npm run build
```

New: `annotation/test_submit_loop.py` (16 tests — A–D, incl. the superseded
upload file actually leaving disk, 403 on paint/submit after approve+lock, and
the decision log outliving its submission) and `accounts/test_people.py`
(15 tests — the per-role derivations, the profile allow-list, the seeded
requesters). `annotation/test_tracking.py`'s hard-case class was rewritten as
`HardCaseApiTests` (24 tests) for the renamed model/URL plus the new
project-scoped visibility, permission, and take-down rules; it now paints real
labels in `setUp` so the "label must exist" guard is exercised rather than
worked around.

**5 pre-existing failures remain in `annotation/test_api_flows.py`
(`DataRegistrationFlowTests`) and `volumes/tests.py`** — all from the
uncommitted `_normalize_label_type` work in `volumes/services.py` (it now
rejects `label_type='proofread'` and requires a non-`none` type when a mask is
registered; those tests haven't caught up). Unrelated to this brief and left
alone.

### Deliberate follow-ups

- **Upload submissions still don't merge into the official mask** on approve —
  unchanged and explicitly out of scope (see `approve_submission`'s docstring).
- **Hard cases carry no note/discussion field.** The brief didn't ask for one;
  a case is currently "look at this label", with no thread.
- `calculate_annotator_workload` (the project page's table) was left as-is;
  People computes its own richer counts rather than widening that helper's
  shape and its existing callers.
- No per-volume hard-case list on `/volumes/:id` — the API supports
  `?volume=`, but only the project section and the inbox were asked for.
- `is_project_member` widens *view* access to any annotator on a project. That
  is intended (it's what makes a shared hard case readable), but it is a real
  broadening of `can_view_task`/`can_view_volume` worth knowing about.
