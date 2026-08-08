# REST API reference

> Part of [progress/](README.md). To add or change an endpoint, see
> [codemap.md](codemap.md#common-recipes-end-to-end); the view/serializer files
> per feature are listed there.

Base URL: `/api`. All endpoints except login/register require a token header:

```
Authorization: Token <token>
```

Obtain a token from `POST /api/auth/login/` or `POST /api/auth/register/`.

Roles: **requester** (registers datasets, views own projects), **annotator**
(works on assigned tasks), **manager** (manages projects, annotators, and
manual task assignment; superusers are treated as managers). Annotation work is
unpaid — there are no payment endpoints.

## Auth

| Method | Path | Access | Body |
| ------ | ---- | ------ | ---- |
| POST | `/auth/login/` | public | `{username, password, portal?}` → `{token, user}`; `portal` ∈ `requester`/`annotator` validates the login tab |
| POST | `/auth/register/` | public | `{username, password, email?, role, institution_name?}` where `role` ∈ `annotator`/`requester` (no public manager signup) → `{token, user}` |
| POST | `/auth/logout/` | auth | — (invalidates token) |
| GET  | `/auth/me/` | auth | → current user |
| GET  | `/annotators/` | manager | list annotators for assignment dropdowns |
| GET  | `/people/overview/` | auth | the whole People page, role-scoped — see [People](#people--who-works-with-whom) |
| PATCH | `/people/me/` | auth | `{display_name?, institution_name?, contact_note?}` → refreshed current user. Role and the institution *link* are administrative and cannot be set here. |
| GET  | `/people/<username>/` | auth | read-only card for one person |

## Data registration (requester + manager, shared endpoint)

| Method | Path | Notes |
| ------ | ---- | ----- |
| POST | `/hpc/scan/` | `{hpc_directory}` → `{directory, files[], pairs[], unpaired[]}`. `pairs` are auto-detected `{image, mask, base}` image+mask matches; `unpaired` are the leftover file names. |
| POST | `/register-data/` | `{dataset, image_directory|hpc_directory, pairs?, files?, label_type?, metadata?, project?, annotation_type?}`. Registers HPC file references as volumes under a dataset. `dataset` and an image directory are required; only supported volume formats are accepted. Requires an existing `project`. Legacy `volume` / pair `chunk_id` fields are ignored or treated as rename aliases. |

Image/mask pairing is flexible:

* `pairs`: explicit `[{image, mask?, name?}, …]` — pick specific image+mask
  pairs out of a folder that also holds unrelated volumes. A `mask` is stored as
  the volume's label (typed by `label_type`, default `prediction`). Optional
  `name` renames the volume (defaults to the case/file id).
* `files`: image-only `[{path|name, chunk_id?}, …]` (no masks; `chunk_id` still
  accepted as a rename alias).
* neither: the directory is auto-scanned and **all detected image+mask pairs
  plus any unpaired images** are registered.

`metadata` is optional biomedical detail (organism, tissue, cell_type,
imaging_modality, imaging_instrument, experimental_condition, sample_condition,
dataset_source, publication, description, notes). Resolution, shape, and
mitochondria counts are derived from the files, never entered here.

## Projects (manager: all; requester: own)

| Method | Path | Notes |
| ------ | ---- | ----- |
| GET | `/projects/` | list (manager: all; requester/Institution: own). `?lifecycle=new\|to_proofread\|done` filters by lifecycle bucket |
| POST | `/projects/` | `{title, dataset?, description?, metadata?, annotation_type?, workflow_type?, deadline?}` (`workflow_type` ∈ annotation/proofreading/segmentation; defaults from `annotation_type`) |
| GET | `/projects/lifecycle-counts/` | `{new, to_proofread, done}` counts over the caller's visible projects |
| GET | `/projects/<id>/` | retrieve (owner Institution or manager). Response includes `workflow_type` and computed `lifecycle` |
| PATCH | `/projects/<id>/` | partial update, incl. `metadata` (owner Institution or manager) |
| DELETE | `/projects/<id>/` | delete |
| GET | `/projects/<id>/summary/` | progress (+ annotator workload for managers) |
| POST | `/projects/<id>/review/` | manager only: `{reviewed?}` (default `true`) — approve Institution-registered data so it can be split/assigned |

## Volumes (manager: any; requester: own project)

| Method | Path | Notes |
| ------ | ---- | ----- |
| GET | `/projects/<project_id>/volumes/` | list |
| POST | `/projects/<project_id>/volumes/` | multipart: `name`, `image_path` or `image_file`, `label_path`/`label_file`, `label_type`, `file_format`, `voxel_size_*` |
| GET | `/volumes/<id>/` | retrieve |
| PATCH | `/volumes/<id>/` | edit metadata / label_type / shape (owner requester or manager) |

There is no endpoint that subdivides a volume: one volume is one assignable
work unit, and its single whole-volume task is created by the Assign flow.

## Tasks

| Method | Path | Access | Notes |
| ------ | ---- | ------ | ----- |
| GET | `/projects/<project_id>/tasks/` | manager | `?status=` filter |
| POST | `/projects/<project_id>/assign-tasks/` | manager | auto-assign: one whole-volume task per volume, distributed evenly across active annotators. Requires a reviewed project (`400` with `reviewed:false` otherwise). |
| POST | `/projects/<project_id>/assign-plan/preview/` | manager | build an **editable** plan without committing: ensures a task per volume, returns `{created_tasks, skipped_volumes, entries[]}` where each entry is a serialized task plus `proposed_annotator_id`. Requires a reviewed project. |
| POST | `/projects/<project_id>/assign-plan/apply/` | manager | commit a manager-edited plan atomically: `{entries:[{task_id, annotator_id?, priority?, difficulty?, instructions?, deadline?}]}` (null/omitted `annotator_id` unassigns). Returns `{updated, assigned, remaining_unassigned}`. |
| POST | `/tasks/<id>/assign/` | manager | manual (re)assign: `{annotator_id}` (null unassigns; updates the task in place) |
| GET | `/tasks/<id>/` | auth | manager: any; annotator: own. Includes dataset + project metadata, plus the **server-decided gates** `can_submit` / `can_annotate`, `annotation_locked`, `submission_count`, and `last_decision*` (see [Submissions](#submissions)) |
| PATCH | `/tasks/<id>/` | auth | manager: any field; annotator: start own task |
| GET | `/my-tasks/` | annotator | work still in their hands: assigned / in_progress / revision_requested / **rejected** (a rejected task is work to redo, not history) |
| GET | `/my-completed-tasks/` | annotator | handed over: submitted / approved |
| POST | `/tasks/<id>/annotation-lock/` | manager | `{locked}` — reopen a task closed on approve, or close one left open. This flag is what `can_submit`/`can_annotate` key off. |
| GET | `/tasks/<id>/proofreading/` | viewer | launch info from the proofreading provider: `{mode, url, editable, download_available, message, provider, download{...}}`. **Server downgrades to view-only** (`editable=false`) for requesters/non-assignees even if the provider advertises `edit`. `mode` ∈ edit/view/download/unavailable |
| GET | `/tasks/<id>/visualization/` | viewer | `{available, url, provider, mode, meta{shape,dtype}, region?, editable}` |

### Visualization + in-app annotation (slice streaming, role-gated)

"Viewer" = manager, the project owner (Institution), or an annotator with a task
on the volume. "Editor" = manager or the assigned annotator. Requesters can
**view** but never mutate — enforced server-side.

**Working copy vs. official label**: `label-slice` (below)
and `meta`'s `has_label` read the volume's *official, approved* label —
edits made through the editor endpoints (`label-ids`, `track`) only ever
touch a separate staging copy and never appear here until a manager approves
the submission referencing them (`POST /submissions/<id>/review/`). The
editor endpoints themselves (`label-ids` GET/PUT, `label-state`, `track`,
plus the read-only `labels-summary` / `labels-3d-mesh`) —
documented in `backend/annotation/MODULE.md`'s "Label persistence" section,
not repeated here — always read/write that staging copy.

| Method | Path | Access | Notes |
| ------ | ---- | ------ | ----- |
| GET | `/volumes/<id>/meta/` | viewer | `{shape{z,y,x}, dtype, axes, has_label, volume_id}` read from headers (no full load); `has_label` reflects the **official** label only |
| GET | `/volumes/<id>/slice/?axis=z&index=..&window=..&level=..` | viewer | one image slice as **PNG**, windowed (brightness/contrast); memmap + bounded LRU |
| GET | `/volumes/<id>/label-slice/?axis=z&index=..&region_only=..` | viewer | one **official, approved** instance-label slice as an **RGBA PNG** overlay — not the in-progress working copy. `region_only=1` renders only the instances that touch the region mask on that plane, and renders each of them **whole** (display filter; writes nothing, and ignored when the volume has no region mask) |
| GET | `/volumes/<id>/region-index/?axis=z` | viewer | `{axis, length, indices}` — every plane of that axis holding any region voxel, so a viewer can jump to the nearest one. Scanned once per mask file and memoized per process; `404` when the volume has no region mask. Also served, identically, at `/public/hard-cases/<token>/region-index/`, `/public/tasks/<token>/region-index/` and `/public/shares/<token>/volumes/<id>/region-index/` |
| POST | `/tasks/<id>/track/` | editor | fork-aware SAM2 tracking. Body `{seeds:[{z, rle:[[start,len]], shape:[h,w]}], z_range?}`. Splits a forked mito into temporary branch tracks, propagates (GPU on `sam2`, CPU on `local`), **auto-merges the group into one instance**, persists to the **working copy** + group metadata (not yet the official label — see above). Requesters get `403`. Returns `{final_id, branch_ids, group{group_id,branch_ids,final_id,seed_z}}` |

## Hard cases — project-scoped, plus an optional public link

An editor records the Active label as a **hard case for its project**. Everyone
on that project — its manager(s), its requester, and every annotator holding a
task on it — sees it in `/hard-cases` and on the project page and opens it at
`/hard-cases/<id>` with no token. The original **unguessable public link**
survives as an extra, for pasting outside the app: anyone with it gets a
view-only viewer soloed on the flagged label, no account needed. See
`progress/history/{02-share-hard-case,05-submit-people-hardcases}.md` and
`backend/annotation/models.py`'s `HardCase`.

Permission matrix (`annotation.services`):

| action | manager | creator | other project member | token holder |
| --- | --- | --- | --- | --- |
| view | yes | yes | yes | yes (unless revoked) |
| annotate | yes | yes¹ | no | no |
| take down / revoke | yes | yes | no | no |

¹ the creator additionally needs live edit access to the underlying task
(`can_annotate_task`) — annotating a case writes the task's working copy, so a
creator whose task was reassigned or locked correctly drops to View-only rather
than getting a button that `403`s.

`status` (`open`/`resolved`) is the in-project lifecycle: **take down resolves,
it never deletes**, so members keep read access to settled cases. `revoked` is
separate and kills only the public token.

| Method | Path | Access | Notes |
| ------ | ---- | ------ | ----- |
| POST | `/tasks/<id>/hard-cases/` | editor | record a case. Body `{label_id}` (the Active instance; must exist in the mask, else `400`). Returns the full case row incl. `app_url` (`/hard-cases/<id>`) and `url`/`token` (the public link). Gated on `can_edit_task` — requesters/non-assignees get `403`. A **locked** task can still have cases recorded. |
| GET | `/hard-cases/` | auth | inbox, newest first. `?project=` / `?volume=` / `?status=` narrow it. Scoped to project membership server-side. |
| GET | `/hard-cases/<id>/` | member | one case, incl. `can_annotate` / `can_take_down` for the caller and the `z_start`/`z_end`/`volume` the canvas needs |
| POST | `/hard-cases/<id>/status/` | creator/manager | `{status}` ∈ `open`/`resolved` — take down or reopen |
| POST | `/hard-cases/<id>/revoke/` | creator/manager | `{revoked}` — kill/restore the **public token only**; members are unaffected |
| GET | `/public/hard-cases/<token>/meta/` | **public** | case identity + volume meta: `{task_id, volume_id, label_id, z_start, z_end, shape, dtype, axes, display_range, has_label, volume_name, project_title}`. Invalid/revoked token → `404`. |
| GET | `/public/hard-cases/<token>/slice/?axis=&index=` | **public** | one image slice (JPEG), same encoding as the authed viewer |
| GET | `/public/hard-cases/<token>/label-state/` | **public** | `{max_label_id, next_label_id}` of the working copy |
| GET | `/public/hard-cases/<token>/label-ids/?axis=&index=` | **public** | raw instance ids of one slice, RLE (read-only; **no PUT** here) |
| GET | `/public/hard-cases/<token>/labels-summary/` | **public** | whole-volume per-label summary (so a recipient can reveal other labels) |
| POST | `/public/hard-cases/<token>/labels-3d-mesh/` | **public** | `{labels:[...]}` → the same iso-surface meshes as `/tasks/<id>/labels-3d-mesh/` — the shared viewer's 3D runs through exactly one backend path, not a share-only fork |
| POST | `/public/hard-cases/<token>/labels-3d/` | **public** | `{labels:[...]}` → the legacy voxel grid, same as `/tasks/<id>/labels-3d/` |

The public views are `AllowAny` **with authentication disabled** (a stale token
in a viewer's `localStorage` never turns a public page into a `401`). They are
**read-only clones** scoped to the case's task/volume — there is deliberately
**no** public write path; the authed editor endpoints (`label-ids` PUT, `track`,
`watershed`, …) still require login and ignore share tokens entirely. There is
no way to enumerate cases without an account; only a known token resolves.

## Processing jobs (manager: all; Institution: own projects)

| Method | Path | Access | Notes |
| ------ | ---- | ------ | ----- |
| GET | `/processing-jobs/` | auth | list; manager sees all, Institution sees jobs on own projects. `?status=` / `?job_type=` filters |
| GET | `/processing-jobs/<id>/` | auth | retrieve |
| POST | `/processing-jobs/<id>/retry/` | manager | requeue a terminal (failed/cancelled/succeeded) job |
| POST | `/processing-jobs/<id>/cancel/` | manager | cancel a job (best effort via its backend) |

Jobs are **created by the service layer** (not a public POST) and executed by the
`run_processing_dispatcher` management command.

## Submissions

Two ways to submit — `source` on the returned/listed submission tells you
which: `upload` (a file) or `inapp` (the in-app-edited working copy, no
file).

| Method | Path | Access | Notes |
| ------ | ---- | ------ | ----- |
| POST | `/tasks/<id>/submit/` | annotator | multipart: `label_file`, `notes?` — creates a `source=upload` submission |
| POST | `/tasks/<id>/submit-inapp/` | editor | `{notes?}`, no file — submits the task's current in-app working label copy for review (`source=inapp`). `400` if nothing has been painted/tracked yet. Gated on `can_submit_task` (manager or the assigned annotator, **and** the task not locked), so a manager annotating directly can also submit. |
| GET | `/submissions/` | auth | manager: all; annotator: own; `?task_status=`. **Latest per task only.** |
| GET | `/submissions/<id>/` | auth | retrieve |
| POST | `/submissions/<id>/review/` | manager | `{decision, comments?, allow_further_annotation?}` where decision ∈ approved/rejected/revision_requested. **Approving a `source=inapp` submission promotes its working copy to the volume's official label** (repoints `label_path`, clears `label_file`) — this is the only place that happens. Approving a `source=upload` submission does not touch the volume's label (unchanged from before this workflow existed). Reject/revision-requested never promote anything either way. |

### The submit ↔ review loop

Both submit paths follow the same rules (`annotation.services`; see
`progress/history/05-submit-people-hardcases.md`):

* **Submit never disappears** while the work is still the annotator's.
  `can_submit_task` = edit access **and** `not task.annotation_locked` — there
  is deliberately **no status list**, so submitting again after a previous
  submit, a reject, a revision request, or an approve-that-stayed-open all
  work. Clients must read `can_submit` off the task rather than re-deriving it.
* **Latest submission wins.** Each submit deletes the task's previous
  submission row *and* any uploaded file it owned, then bumps
  `task.submission_count`. `GET /submissions/` shows one row per task.
  `ReviewRecord` outlives the submission it decided (`submission` is
  `SET_NULL`, `task` is the durable link), so the decision log survives the
  pruning — that is what the People panels count.
* **Approve applies the manager's switch.** `allow_further_annotation=false`
  (the default — approve means done) sets `annotation_locked`: painting and
  submitting both `403`, and the editor drops to View-only. `true` promotes the
  mask but leaves the task open; a further submit starts another round.
  `POST /tasks/<id>/annotation-lock/` flips the flag afterwards.
* **Reject and revision hand the task back**, unlocked, with nothing promoted.
  They keep distinct statuses (`rejected` / `revision_requested`) because the
  verdict is worth showing, but neither status gates anything —
  `annotation_locked` is the single gate.

## People — who works with whom

One role-scoped endpoint behind the `/people` page. Membership is
**project-centric**: nobody is "assigned to a manager" in the schema; people are
related because they share a project. Every role gets the same payload shape
(empty lists where a role has no such panel), so the client renders panels by
which lists are non-empty. See `backend/accounts/services.py`.

```
GET /api/people/overview/
{
  "me": {…person…, "stats": {…}},   "role": "annotator",
  "managers":   [ … ],   // annotator/requester: managers of their projects
  "peers":      [ … ],   // annotator: peer annotators on shared projects
  "annotators": [ … ],   // manager: roster + workload + last decision
  "requesters": [ … ],   // manager: customers + the projects they registered
  "projects":   [ … ]
}
```

A person card is `{id, username, display_name, role, institution_name,
contact_note, email, projects[], stats{}}`. Annotator `stats` cover
assigned/active/submitted/approved/rejected, `submissions` (how many times they
handed work over — survives latest-only pruning), `reviews_approved` /
`reviews_rejected`, `last_decision`, and open hard cases.

Managers of a project are read from the project itself (`created_by` when that
user is a manager, plus `reviewed_by`); a project nobody has reviewed yet falls
back to "every manager", because an annotator asking "who do I hand this to?"
deserves an answer rather than an empty list.
