# Codex Prompt — Manager roster, viewer UX, durability, concurrency, hard-case notes, smooth scrubbing

> **How to use:** Paste this entire document into Codex as the task brief.
> Work tree: `/home/weidf/shenb/mito-data-studio`
> Reference (same feature surface, do not fork logic): `/home/weidf/shenb/mito-data-studio-production-v1.1.1`
>
> Re-verify every cited path before editing. Do not push, deploy, restart prod,
> or mutate live annotation volumes unless explicitly asked.

---

## Mission

Implement **ten** product fixes in `mito-data-studio` so managers and annotators
stop hitting roster divergence, empty “Jump to region”, watershed failures /
confusing Seeds UI, Region-only save gaps, missing Labels context actions,
lost verified state, missing canvas “Show 3D”, shortcut / multi-user
interference, hard cases without notes, and stuttery layer scrubbing.

Prefer shared backend truth over duplicated UI glue. Keep diffs focused.
Update / add tests for each item. Update the relevant guide under `docs/guides/`
when behavior changes.

---

## Product context (current reality — fix against this)

Stack: Django 5 + DRF backend, React 18 + Vite SPA, Three.js 3D meshes.

Today these are **three separate systems**:

| System | Purpose | Storage | Assignable? |
|---|---|---|---|
| **Access members** (`ProjectMembership`) | Browse project + Hard Cases without a task | `projects_projectmembership` | No |
| **Teams & assignment eligibility** (`Team` / `TeamMembership` + `Project.working_team`) | Who may receive volume assignments | `accounts_team*`, `projects_project.working_team_id` | **Yes — only path** |
| **Task assignment** (`AnnotationTask.assigned_to`) | Actual workload | `annotation_annotationtask` | Listed in Access as “Via assigned task” |

Manager pain today: adding someone under Project → **Access members** does **not**
make them appear under People → **Teams & assignment eligibility → Add annotator**,
and they **cannot** be assigned work. Only people added via Teams / working-team
are assignable. Managers want **one operation** to mean both (or shared backend
so either UI updates the same eligibility set).

---

## Required outcomes (acceptance criteria)

### (1) Unify Access members ↔ Add annotator / assignment eligibility

**Goal:** Manager operates in **either** Project → Access **or** People → Teams
& assignment eligibility, and the person becomes eligible for assignment (and
visible in both places) without a second step.

**Desired product rule (implement this):**

1. Adding a person via **Access members** must also ensure they are on the
   project’s **working team** (create / attach default team membership as needed).
2. Adding a person via People → Teams (“Add annotator” / team member add) for
   the project’s working team must also ensure they show up in Access (either
   via working-team union already shown, or by writing explicit membership —
   prefer **one shared service** so lists cannot diverge).
3. Removing from assignment eligibility vs removing browse-only access must be
   intentional and documented in UI copy — but the **happy path** is one
   shared roster for “people who can be given work on this project”.
4. `AssignmentPlanEditor` assignee dropdown must include anyone added through
   either UI (still gated by `is_eligible_project_assignee`).

**Touchpoints (re-verify):**

- Backend: `backend/projects/models.py` (`ProjectMembership`),
  `backend/projects/api.py` (`members` / `remove_member`),
  `backend/accounts/teams.py` (`add_team_member`, `set_project_working_team`,
  `is_eligible_project_assignee`),
  `backend/accounts/collaboration_api.py` / `api.py`,
  `backend/annotation/services.py` (`is_project_member`)
- Frontend: `frontend/src/pages/ProjectDetailPage.tsx` (`ProjectMembers`),
  `PeoplePage.tsx`, `CollaborationManager.tsx`, `teams/TeamEditor.tsx`,
  `MemberPicker.tsx`, `AssignmentPlanEditor.tsx`,
  `frontend/src/api/projects.ts`, `collaboration.ts`
- Docs: `docs/guides/tasks-and-assignment.md`, `accounts-and-roles.md`
- Tests: `backend/projects/test_members_api.py`, `accounts/test_teams.py`,
  `accounts/test_collaboration_api.py`, related frontend page tests

**Implementation guidance:**

- Introduce (or extend) a single service e.g. `ensure_project_assignee_eligible(project, user, *, actor)` that:
  - ensures a working team exists / is set on the project if missing,
  - adds `TeamMembership`,
  - optionally upserts `ProjectMembership` for browse access,
  - is called from **both** Access POST and Teams “add member”.
- Do **not** invent a third roster table.
- Keep permission checks; managers only.
- Update UI copy so Access is no longer described as “assignment stays separate”
  if the new rule makes Access also confer eligibility.
- Migration / backfill: for existing projects, consider a management command or
  migration note to sync explicit Access members → working team (ask before
  destructive backfill of prod data; ship the forward path + a dry-runnable
  backfill script).

---

### (2) Fix “Jump to region” jumping to empty planes

**Bug report:** Clicking **Jump to region** landed on a plane with **no region**.

**Expected:** Never navigate to a plane that has no region pixels for the
**current axis**. If current plane already has region → no-op with clear title.
If no plane has region → disabled / message. Otherwise jump to nearest
non-empty region plane only.

**Touchpoints:**

- `frontend/src/features/viewer/JumpToRegionButton.tsx`
- `frontend/src/features/viewer/regionIndex.ts` (`nearestRegionIndex`, cache)
- `backend/annotation/api.py` (`VolumeRegionIndexView`, `_region_index_payload`)
- `backend/volumes/region_masks.py` (`region_nonempty_indices`,
  `calculate_region_nonempty_indices`)
- Docs: `docs/guides/region-only.md`

**Investigate and fix:**

1. Stale `sessionStorage` / memory cache returning wrong indices for
   `(volumeId, axis)` after mask rebuild or axis switch races.
2. Axis mismatch (using another axis’s index list).
3. Off-by-one between 0-based API indices and UI display / `onJump`.
4. Backend scanning emptiness incorrectly (threshold, dtype, orientation).
5. Jumping before prefetch completes using incomplete data.

Add regression tests: empty volume, single plane, two distant blocks, current
already in set, axis switch invalidates cache, never returns an index absent
from the nonempty list. After jump, optionally assert region pixels exist on
that plane (frontend unit + backend API test).

---

### (3) Watershed: voxel limit on “small” labels + Seeds UI

#### 3a — Error: “Target crop has 39,231,062 voxels; bounded tool limit is 32,000,000”

Source: `backend/annotation/services.py` → `_load_label_crop` /
`MITO_TOOL_PLAN_MAX_VOXELS` (default `32000000`), used by
`plan_watershed_task` / `plan_split_components_task`.

**User report:** Happens when watersheding what looks like a **small** label.

**Do:**

1. Diagnose why bbox is huge for a visually small instance (wrong target label,
   label id reused across distant components, padding, whole-volume scan bug,
   pending-slice merge expanding bbox, etc.).
2. Fix the root cause if it is a bug.
3. If the instance truly is large in 3D, improve UX:
   - clearer error (“label spans Z×Y×X ≈ …; place seeds on a smaller object or
     crop/ROI”),
   - and/or smarter crop: bbox around **seeds ± padding** intersected with
     target-label mask, not necessarily the full target-label AABB when that
     AABB exceeds the plan limit,
   - still refuse unsafe OOM plans; do not silently raise the global limit
     without measuring memory.
4. Keep existing tests in `backend/annotation/test_whole_volume_ops_api.py`;
   add cases for seed-local crop and for true oversize refusal.

#### 3b — Seeds tool should not need Active / New

Today `AnnotateToolChrome.tsx` shows **Active** + **New** for Seeds (only
hidden for split/merge/delete). Watershed already tracks `wsTargetLabel` from
seed clicks.

**Desired:** In Seeds mode, hide Active/New. Running watershed should
**automatically allocate the smallest unused new label id** for split-out
components (same semantics as today’s **New** button: smallest free id
including unsaved paint / holes from merge-delete — see New button title).
Do not require the annotator to manually set Active=613 / click New.

Confirm backend `run_watershed_3d` / `plan_watershed_task` already uses
`max_existing_label` / next ids; align frontend so UI does not imply Active is
the destination id unless that is truly how the algorithm works — if Active is
only the *target* to split, seeds-on-instance should define target; new ids
auto-assigned.

---

### (4) Region only: allow saving split / watershed results that extend outside the mask

**Bug / gap:** With Region only on, Save protects outside-ROI pending voxels
(`AnnotationCanvas` save path + `protect_*_outside_roi`), so split/watershed
pieces that land outside the region mask may not persist — or appear to vanish
after save / toggle.

**Desired behavior:**

1. After Split or Watershed (and after Save), new labels must **not disappear
   immediately**, even if parts lie outside the region mask.
2. While Region only remains on in the same editing session, keep showing those
   newly created labels so the annotator can finish the operation.
3. If the user turns Region only **off** and later **on** again, re-apply the
   strict volume-wide membership rule from `docs/guides/region-only.md`:
   a label that **never touches** the region anywhere is **hidden** again and
   need not be shown in Region only mode.
4. Ensure Save actually writes the voxels for split/watershed results that the
   user is allowed to keep (including outside-mask voxels that belong to a
   label created/edited in this session, or whatever precise rule you choose —
   document it). Do not leave “saved on screen, missing on disk”.

**Touchpoints:**

- `frontend/src/features/viewer/AnnotationCanvas.tsx` (save, ROI membership,
  pending outside staging, split/watershed apply)
- `backend/annotation/region_mask.py` (`protect_volume_outside_roi`,
  `protect_slice_outside_roi`)
- Docs: `docs/guides/region-only.md` — update “Saving while focused”

Add tests for: watershed/split creating outside voxels → save with Region only
on → reload → voxels present; toggle Region only off/on → pure-outside label
hidden.

---

### (5) Labels panel: right-click → Verify / Unverify

Today Verify/Unverify exist as toolbar buttons for the **active** id
(`LabelsPanel.tsx`) and as canvas context “✓ Verify label {id}”
(`AnnotationCanvas` context menu). There is **no** right-click menu on Labels
list rows.

**Desired:** Right-click a row in **Labels** → context menu with at least:

- **Verify**
- **Unverify**

Reuse existing `onLifecycleAction` / `POST …/labels/<id>/lifecycle/` — no new
lifecycle model. Disable Unverify when not verified; disable Verify when
already verified (or make Verify idempotent). Keep keyboard **F** behavior.

Optional but nice: include Solo / Show 3D in the same menu for consistency
with canvas — not required unless cheap.

---

### (6) Persist verified mitochondria across reopen (annotator bug)

**Annotator report:** “Whenever I open up a volume I was working on, the
mitochondria I previously verified has now been unverified.”

There is already a JSON sidecar via `LabelMetadataStore`
(`backend/annotation/cellable_port/label_state.py`,
`working_label_metadata_rel_path`, `set_label_lifecycle_action`).

**Do not assume “feature missing” — treat as durability bug:**

1. Trace verify → `_save_label_metadata_store` → path on disk → reopen →
   `get_labels_summary` load.
2. Check whether Save / Reset / task reopen / working-copy reseed /
   metadata path migration (`legacy_working_label_metadata_rel_path`) drops or
   fails to load the sidecar.
3. Check whether client state overwrites server verified flags on load.
4. Fix so verified state survives close/reopen of the same working volume/task
   unless the annotator Unverifies or Reset annotations (Reset may clear
   verification by design — document that).
5. Add an integration test: verify label → new API session / reload summary →
   still `verified` with `verified_at`.
6. If useful, add a lightweight “autosave metadata on verify” confirmation in
   UI (toast/status) so annotators know it stuck — without requiring a full
   mask Save if metadata already persists independently (confirm current
   behavior and keep it).

---

### (7) Canvas right-click mito → “Show 3D”

Today canvas context menu (`CONTEXT_MENU_LAYOUT` / `AnnotationCanvas.onContextMenu`)
offers tools + Verify + Solo when a label is under the cursor. **3D** pin exists
only in Labels panel (`LabelViewButtons`).

**Desired:** When right-click hits a mito label, add **Show 3D** (and if already
pinned, **Hide 3D** or toggle label). Wire to the same pin set used by the
Labels panel / `Labels3DPanel` (`pinned3D` / `label3DIds`). Opening 3D view if
collapsed is OK if that matches existing “3D” button behavior.

---

### (8) Per-account Annotate shortcuts isolation + multi-user stability

**User ask:** Ensure each account’s **Annotate shortcuts** are isolated and do
not interfere. Different users must be able to keep and use **different** saved
shortcut maps at the same time. Multiple users operating concurrently must not
crash the app — harden stability even if much of this already exists.

**Current design (re-verify, do not regress):**

- Shortcuts live on `UserProfile.annotate_shortcuts` (JSON), not browser
  `localStorage` — see `backend/accounts/shortcuts.py`,
  `accounts/models.py`, migration `0009_userprofile_annotate_shortcuts.py`,
  profile PATCH via `accounts/services.py`, docs
  `docs/guides/keyboard-and-tips.md` (“stored on your account”).
- Editor reads `authUser?.annotate_shortcuts` in `AnnotationCanvas.tsx` and
  resolves via `toolForShortcut` / `annotate/shortcutKeys`.
- Tests already exist: `backend/accounts/test_annotate_shortcuts.py`.

**Required hardening / acceptance:**

1. **Isolation:** User A’s saved map never applies to User B’s session (same
   browser profile / shared machine / simultaneous logins in different
   browsers). No global module singleton that caches “the” shortcut map across
   auth users. On login / `/api/me` refresh / logout, the editor must reload the
   correct map (or clear it).
2. **Simultaneous use:** Two annotators with different bindings can annotate
   different tasks at the same time without one overwriting the other’s
   profile shortcuts or causing cross-talk in the SPA auth store.
3. **Concurrency / crash resistance (broader than shortcuts):**
   - Two users (or two tabs) hitting the **same** task/volume working copy:
     prefer clear conflict handling (lock, 409, or last-write-wins with
     warning) over process crash, truncated HDF5/Zarr, or corrupt sidecar.
   - Concurrent saves / lifecycle / watershed / slice writes must not take
     down the worker (catch, lock, or serialize filesystem writes on a volume).
   - Audit existing locking around working-label mmap / slice_io LRU
     (`backend/annotation/visualization/slice_io.py`) and any task edit APIs;
     add tests where missing (`test_operations.py` already has some concurrent
     append cases — extend for mask write / metadata if needed).
4. If isolation is already correct, still add regression tests that prove two
   profiles keep distinct maps under concurrent PATCH + annotate key handling,
   and document the multi-tab same-task policy in `docs/guides/`.

**Do not** move shortcuts back into shared `localStorage` without a user id key.

---

### (9) Hard case record: optional note / reason

**User ask:** When recording a hard case, allow a **small note** explaining why
it was recorded.

**Current:** `HardCase` has no note field (`backend/annotation/models.py`).
Create flow is confirm modal → `POST /api/tasks/<pk>/hard-cases/` with
`{"label_id"}` only (`HardCaseCreateView`, `createHardCase` in
`frontend/src/api/hardCases.ts`, confirm UI in `AnnotationCanvas.tsx`).
List/detail types: `frontend/src/types/hardCase.ts`.

**Desired:**

1. Add `note` (TextField, blank OK, reasonable max length e.g. 500–2000 chars)
   on `HardCase` + migration.
2. Confirm dialog: optional textarea “Why is this hard?” (or similar); empty
   allowed so existing one-click habit still works.
3. Persist on create; return in `HardCaseSerializer`; show on Hard Cases list
   row (truncated) and Hard Case detail / share pages.
4. Only creator/managers need edit-later if you add PATCH; **create-time note
   is enough** for v1 unless cheap.
5. Docs: `docs/guides/sharing-and-hard-cases.md` (or equivalent).
6. Tests: create with/without note; note appears in list/detail API.

---

### (10) Smooth, consistent layer scrubbing (ITK-SNAP / napari-like)

**User ask:** Switching layers often runs smooth for a few pages then stutters
for a few. Optimize so A/D (and scrub) stay **consistently fluid**, closer to
ITK-SNAP / napari-style continuous paging — not “butter then hitch”.

**Current touchpoints:**

- Annotate path: `AnnotationCanvas.tsx` slice LRU (`sliceImgCacheRef`,
  `sliceRunsCacheRef`), ~100ms coalesce on index change, neighbor prefetch
  `index±1` and `+2` after 250ms settle (`prefetchAbortRef`).
- View path: `SliceViewer.tsx` `BlobLRU` (Cellable-style
  `MAX_SLICE_PIXMAP_CACHE`).
- Server: `backend/annotation/visualization/slice_io.py` LRU page cache;
  optional chunk/streaming renderer (phase-14 flags).
- Architecture notes: `progress/architecture.md` (sliceCache pattern);
  WK research pack has chunk-cache phases — **prefer incremental viewer
  wins**, not a full WEBKNOSSOS rewrite, unless a small reuse is clearly
  better.

**Required approach:**

1. **Measure** before/after: time-to-paint for sequential A/D over ≥50 layers
   (cold + warm cache); note hitch pattern (GC from URL.revokeObjectURL?
   label RLE decode on main thread? prefetch starvation? server LRU thrash?
   AI embedding warm fighting slice IO?).
2. **Fix stutter root causes**, e.g.:
   - Widen / tune prefetch window and keep it **ahead of** the scrub
     direction (directional prefetch when holding A or D).
   - Avoid cancelling useful in-flight neighbor fetches on every step
     (already partly designed — verify scrub still does not thrash).
   - Raise or smarter-size client LRU so revisiting recent planes stays warm
     without blowing RAM; revoke URLs off the critical path if needed.
   - Decode / overlay work: skip redundant full overlay rebuilds when only
     index changed and cached runs exist; consider yielding to rAF.
   - Server: ensure slice endpoint + `slice_io` cache stay hot under sequential
     reads; avoid per-request process cold-open of huge volumes.
   - Do not fire expensive AI `warmEmbedding` storms while the user is only
     scrubbing layers (already coalesced — verify it cannot stall paint).
3. Goal UX: holding A/D feels even; brief placeholder OK if marked, but
   **avoid multi-layer freezes**. Target: warm-cache steps stay interactive
   (aim for consistent sub-frame or low-tens-of-ms UI updates; document real
   numbers you achieve).
4. Add a small perf note or checklist under `docs/` / `progress/` with the
   before/after method; optional micro-benchmark harness if one already exists
   in the WK transformation benchmarks folder.
5. Keep correctness: never show the wrong axis/index plane; revision gates on
   label reads after Save must remain.

---

## Engineering constraints

- Primary edit tree: `/home/weidf/shenb/mito-data-studio`
- `mito-data-studio-production-v1.1.1` is the deployed twin for behavior
  reference; feature code is effectively the same (mainly styles/runtime
  differ). Implement in the main tree; do not maintain divergent forks.
- Match existing patterns, naming, and test style.
- No unrelated refactors; no drive-by CSS redesign.
- No secrets in commits; do not commit `.env`, `var/`, live masks.
- Do not run destructive git commands; do not push unless asked.
- After implementation, run the focused backend/frontend tests for touched
  areas; fix failures you cause.

## Suggested implementation order

1. (6) verified persistence — data-loss bug  
2. (2) Jump to region — navigation bug  
3. (8) shortcut isolation proof + concurrent-write crash hardening  
4. (4) Region only save / visibility for new splits  
5. (3) Watershed crop + Seeds UI  
6. (10) layer-scrub smoothness (measure → tune cache/prefetch)  
7. (5) + (7) context menus; (9) hard-case note  
8. (1) Access ↔ Teams unification (largest product/API change)

## Deliverables

1. Code + tests implementing all ten items (or clearly list any blocked item
   with evidence).
2. Short `progress/` or PR-style summary: what changed, how to verify manually.
3. Doc updates for assignment, region-only, hard cases, shortcuts/concurrency,
   and layer navigation (+ annotation-tools if Seeds UX changed).
4. For (1), note any one-time backfill command and whether it was run.
5. For (10), include a short before/after scrub measurement note.

## Manual QA checklist

- [ ] Access: add annotator A → appears in People/working team → assignable on Assign tab  
- [ ] People: add annotator B to working team → appears under Access → assignable  
- [ ] Jump to region never lands on empty region plane; axis switch still correct  
- [ ] Seeds: no Active/New; run watershed on small mito succeeds or errors clearly; new ids auto  
- [ ] Region only on → watershed/split → Save → reopen → outside-extent voxels kept; toggle off/on hides never-touching labels  
- [ ] Labels list right-click Verify/Unverify works  
- [ ] Verify → close volume → reopen → still verified  
- [ ] Canvas right-click mito → Show 3D pins mesh  
- [ ] Two accounts: different Annotate shortcuts; neither picks up the other’s map after refresh  
- [ ] Two users/tabs stressing saves on distinct tasks stay up; same-task conflict is safe (no crash/corrupt mask)  
- [ ] Record hard case with a short note → note visible in inbox/detail  
- [ ] Hold A/D across dozens of layers: hitch pattern reduced vs baseline; warm cache stays fluid  

---

## Start now

1. Audit the cited paths and write a brief plan (files + approach per item).
2. Implement in the order above.
3. Run tests; fix regressions.
4. Stop with a concise summary of diffs and remaining risks.
