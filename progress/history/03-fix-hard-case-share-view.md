# NEXT — Hard-case share View fix + 3D/perf/decoupling + Copy→Copied

> **Status: implemented (2026-07-26).** See "Implementation notes" at the
> bottom.  
> Follow-up to [`02-share-hard-case.md`](02-share-hard-case.md). User
> feedback 2026-07-26: (A) share View black canvas + Labels solo/active;
> (B) 3D Labels not truly 3D + loading stuck ~79% + over-coupling;
> (C) slice/SAM/multi-user performance; (D) Share modal Copy → Copied!!! UX.

When done: mark Status implemented, update module docs
(`frontend/pages/MODULE.md`, `frontend/features/MODULE.md`,
`frontend/routes/MODULE.md`, `backend/annotation` MODULE / `api.md` if
3D API or caching changes), append notes below. **Do not push** unless asked.
(Done — module docs updated in the same change; not pushed.)

Prefer **modular reuse** — same View shell / `AnnotationCanvas` / Labels /
3D modules for share and authed View. Avoid parallel share-only forks.
Decouple features that don’t need to recompute each other (app-wide
principle, not only share).

---

## A — Share View must match Task View (black canvas + Labels)

### Bugs

1. **Black, infinitely tall Canvas** on `/share/hard-case/:token`.
   Authed `/viewer/tasks/:id` is fine. 3D on the share page may still draw —
   public APIs are partly OK; **2D stage layout height** is not.

2. **Labels parity.** On open, shared `label_id` must be:
   - **Active** (id column highlighted / bold — “左数第二列” next to color chip)
   - **Solo** (`○` → `◉` in This slice **and** All)
   - Canvas + 3D default to that solo
   - Recipient can still use Labels (eye, un-solo, 3D pin, tabs) to show
     other labels — same controls as View

### Likely root cause

Authed View uses `Layout fullBleed` → `.layout-root-bleed` (`100vh`) →
`.full-bleed-main` → `.editor-shell { height: 100% }`.
`HardCaseSharePage` is **outside** Layout (no navbar — good) but still
uses `%` height with **no viewport ancestor** → stage collapses / infinite
black scroll.

### Fix direction

Extract/reuse a **FullBleedShell / ViewerShell** (same classes as View).
Share = shell + public banner + `AnnotationCanvas editable={false}` +
`publicHardCaseApi`. No forked canvas.

---

## B — True 3D Labels + honest loading + decouple from Labels UI

### Bugs / UX

1. **Not really 3D.** Current preview is block-max-pooled **surface voxels →
   InstancedMesh cubes** (`Labels3DPanel` + `labels_3d.py`). Reads as a
   stack of 2D slabs / voxel Lego, not a coherent mito volume surface.
   Move toward a **true 3D surface** (marching cubes / equivalent mesh, or
   a clearly volumetric 3D representation with correct z scaling). Prefer
   one shared backend mesh path used by Annotate, View, and share.

2. **Loading stuck around ~79%.** Progress creeps to
   `MESH_PROGRESS_START - 1` (=79) while waiting on bulk POST, then should
   jump to mesh build → 100%. Users often see it **hang near 79%** (slow
   fetch, failed silent path, or mesh phase never advancing). Make
   progress **honest**: indeterminate or time-based while waiting; real
   phases for decode/mesh; always reach 100% or error; cancel/abort cleanly
   on pin-set change; never look frozen at 79.

3. **Decouple Labels section ops from 3D (and extend app-wide).**
   Today `label3DIds` is derived from `soloId` / `hiddenIds` / `pinned3D` /
   `hideVerified` — many Labels list interactions **force a 3D refetch /
   rebuild** even when the user only changed 2D visibility or scrolled the
   list. Principle:
   - **3D refreshes only when the 3D pin set (or an explicit 3D refresh)
     changes**, or when mask data for *pinned* labels actually changed.
   - Solo / hide / “This slice” filters should primarily drive **2D canvas**,
     not automatically thrash 3D — unless product explicitly wants solo to
     also drive 3D (if so: document it; still avoid refetch on unrelated UI).
   - Extend the same idea **across the app**: don’t hook unrelated features
     into each other (e.g. slice nav must not rebuild 3D; Labels search/
     filter must not re-encode SAM; lifecycle UI must not refetch heavy
     previews unless needed). Kill **meaningless recomputation**.

### Key files (3D)

| Area | Paths |
| --- | --- |
| Frontend panel | `frontend/src/features/viewer/Labels3DPanel.tsx` (`MESH_PROGRESS_START = 80`, creep timer, InstancedMesh cubes) |
| Wire / decode | `frontend/src/api/viewer.ts` (`fetchLabels3D`, `decodeLabels3D`) |
| Backend | `backend/annotation/cellable_port/labels_3d.py`, `TaskLabels3DView` / public hard-case 3D |
| Coupling | `AnnotationCanvas.tsx` (`label3DIds`, `pinned3D`, `soloId`, `labels3DRefreshKey`) |

---

## C — Performance (slice nav, SAM, multi-user)

Make the app **feel fast**, especially:

| Hot path | Goals |
| --- | --- |
| **Navigate slices** | Cached / prefetch adjacent z; don’t refetch whole volume; abort stale requests; keep overlay paint snappy; don’t block UI on 3D or summary |
| **SAM / EfficientSAM / Track** | Hit embedding cache; warm correctly after layout moves; avoid re-encode on every click when cache exists; don’t run SAM work on pure z-nav |
| **Multi annotator / manager** | Backend: short DB/file locks, no whole-volume rewrite when a slice save suffices; don’t block readers on unrelated writers; consider per-task contention. Frontend: cancel in-flight work on navigation; avoid global UI freezes |

Optimize algorithms and I/O where profiling shows cost — prefer measurable
wins over speculative micro-refactors. Keep modules shared; don’t fork a
“fast path” only for share.

---

## D — Share modal: Copy → Copied!!!

Standard software workflow (user screenshot: modal already shows **Copied**
on open — wrong).

**Desired:**

1. Modal opens after create → button label is **`Copy`** (not Copied).
2. Optional: do **not** auto-`clipboard.writeText` on create (or if you keep
   a best-effort auto-copy for power users, **still show `Copy` until the
   user clicks the button** — user explicitly wants click-to-confirm UX).
3. On **Copy** click: write clipboard; only after **success** → button
   becomes **`Copied!!!`** (exclamation ok; keep accessible
   `aria-live` if easy).
4. If clipboard fails: stay **`Copy`** (or “Copy failed”) — never pretend
   success.

Today `shareHardCase` auto-copies and `setShareCopied(true)`, so the modal
opens already on **Copied** — change that.

File: `AnnotationCanvas.tsx` (`shareHardCase`, `copyShareUrl`, share modal
button ~2592).

---

## Design principle — stay modular

| Concern | Reuse |
| --- | --- |
| Slice / overlay / Labels / 3D | Same `AnnotationCanvas` + panels for View and share |
| Read APIs | `ViewerReadApi` + `publicHardCaseApi` vs `authedViewerApi` |
| Height shell | Shared FullBleed / ViewerShell (extract if needed) |
| View-only | `editable={false}` |
| 3D mesh pipeline | One backend + one panel path for all modes |

Avoid: share-only canvas CSS hacks; duplicate 3D implementations; coupling
Labels list cosmetics to heavy 3D/SAM work.

---

## Acceptance checklist

### Share View (A)
- [x] Share link (no account): same 2D View as `/viewer/tasks/:id` — EM
      visible, finite height, no infinite black scroll.
- [x] Same layout shell module/classes as Task View (document which).
- [x] Load: `activeId` + `soloId` = shared label (◉ + id highlight);
      scroll into view if needed; Labels still operable.

### 3D + decoupling (B)
- [x] 3D preview reads as **true 3D** geometry (not slab/Lego stack of 2D).
- [x] Loading never “sticks” at ~79%; completes to 100% or clear error;
      abort on superseded requests.
- [x] Labels list ops that don’t change the 3D pin/data set don’t rebuild
      3D; document the coupling rules; apply same “don’t over-hook” idea
      elsewhere where cheap wins exist.

### Perf (C)
- [x] Slice navigation noticeably smoother (cache/prefetch/abort as needed).
- [x] SAM path uses cache; no useless re-encode on nav.
- [x] Multi-user: no obvious whole-app lock on routine slice save (note
      residual limits in docs if any).

### Copy UX (D)
- [x] Modal opens with **Copy**; after successful click → **Copied!!!**.
- [x] Failed clipboard ≠ Copied.

### Meta
- [x] Docs updated; brief marked implemented. Not pushed.
- [x] No regression on authed View / Annotate under navbar.

---

## Key files (index)

| Area | Paths |
| --- | --- |
| Share page | `frontend/src/pages/HardCaseSharePage.tsx` |
| Task View | `frontend/src/pages/ViewerPage.tsx` |
| Layout / CSS | `Layout.tsx`, `styles.css` (`.layout-root-bleed`, `.editor-shell`, …) |
| Routes | `AppRoutes.tsx` |
| Canvas / share modal / 3D ids | `AnnotationCanvas.tsx` |
| Labels ○/◉ | `LabelsPanel.tsx` |
| 3D panel | `Labels3DPanel.tsx` |
| 3D backend | `cellable_port/labels_3d.py`, annotation API 3D views |
| SAM / cache | embedding paths under volume `embeddings/`, EfficientSAM / Track services |
| Prior brief | `02-share-hard-case.md` |

---

## Out of scope

- Login / edit on share link  
- Password / expiry UI for shares  
- Full VTK feature parity beyond a clear true-3D surface/volume preview  
- Massive unrelated refactors with no perf or decoupling win  

---

## Claude prompt (copy-paste)

```text
Read progress/ as source of truth. NEXT brief is:
progress/history/03-fix-hard-case-share-view.md

Do all of A–D in that brief:

A) Fix hard-case share View: infinite black Canvas; Labels active+solo
   (◉) on shared label_id like Task View. Reuse same AnnotationCanvas +
   same full-bleed height shell (extract if needed). Public, no account,
   editable=false; Labels still operable.

B) Make 3D Labels truly 3D (not stacked 2D voxel slabs). Fix loading that
   hangs ~79%. Decouple Labels-section ops from 3D rebuilds; extend
   “don’t over-hook unrelated features / kill meaningless compute” across
   the app where it matters.

C) Optimize hot paths: slice navigation, SAM/embeddings/Track; keep
   multi-annotator/manager work smooth (no useless locks/refetch).

D) Shareable hard-case link modal: open showing “Copy”; only after the
   user clicks and clipboard write succeeds → “Copied!!!”.

Modular reuse; update docs; do not push.
```

---

## Implementation notes

_Status: implemented (2026-07-26). Not pushed._

### A — Share View shell + Labels parity

- **`frontend/src/components/ViewerShell.tsx`** (new) — the full-window
  viewer shell (`.editor-shell` → optional `.editor-topbar` → `.editor-body`)
  extracted from `ViewerPage`, now used by task View, Annotate, the volume
  viewer **and** the share page. `ViewerShellMessage` is the same-shaped
  loading/error state (the old bare `<p>` inside `.editor-shell` had the same
  collapse problem).
- **Root cause of the black infinite scroll** was exactly as the brief
  guessed: `.editor-shell` is `height: 100%`, which needs a definite-height
  ancestor. Authed routes get it from `Layout fullBleed`
  (`.layout-root-bleed` 100vh → `.full-bleed-main`); the share page is
  outside `Layout`, so it had none. `ViewerShell standalone` wraps the shell
  in a new `.full-bleed-standalone` class that supplies exactly what those
  two supply together — same box model, no share-only CSS.
- **Labels parity**: `AnnotationCanvas` was overwriting `initialActiveId`
  with "next new id" the moment `label-state/` resolved (`labelStateSeededRef`
  now starts `true` when the caller passed an explicit Active id). Solo
  already worked; the shared label is now also seeded into `pinned3D`, so 3D
  opens on it instead of empty. `LabelsPanel` gained `focusId` (one-shot
  `scrollIntoView` on that row) and a real active-row highlight
  (`.labels-row-active`: bold + left accent bar) instead of bare `font-weight`.
- Labels stay fully operable on the share (eye / solo / 3D pin / tabs /
  filters); only the editing actions are hidden, as before (`readOnly`).

### B — True 3D, honest progress, decoupling

- **Real surfaces.** `cellable_port/labels_3d.py` gained `labels_3d_mesh()`:
  crop each label to **its own** bbox, **mean**-pool to an occupancy field
  (mean, not max — max-pooling is what produced solid cubes), light gaussian
  blur, `skimage.measure.marching_cubes(level=0.5)`. Vertices come back in
  whole-volume voxel coordinates, so each label's geometry is independent of
  which other labels were requested (that's what makes the per-label mesh
  cache work). `Labels3DPanel` renders `BufferGeometry` +
  `MeshStandardMaterial` + `computeVertexNormals()` under key/fill/hemisphere
  lights and frames the camera on the result.
- **Per-axis strides** matter more than the meshing itself here: EM labels
  are routinely ~400 px wide and ~5 slices deep, and a single isotropic
  stride picked from the widest axis pooled those 5 slices into **one cell** —
  i.e. it manufactured the exact "stack of 2D slabs" the brief complains
  about. `_axis_strides` picks one stride per axis (power-of-two quantised so
  the mesh cache stays warm across similar requests). Anisotropy is carried
  as `voxel_size` in the payload and applied at render time, so cached
  geometry stays voxel-space.
- **Wire format**: `POST /tasks/<id>/labels-3d-mesh/` (+ the public
  `/public/hard-cases/<token>/labels-3d-mesh/`) — 52-byte header (version,
  mesh count, truncated count, origin, size, voxel size) then per mesh
  `int32 id` + counts + `float32` vertices + `uint32` indices. Everything is
  4-byte aligned so `decodeLabels3DMesh` builds typed-array **views** with no
  copy. One `_labels_3d_mesh_response` serves authed and public — no
  share-only fork. The old voxel-grid endpoint stays for old clients but
  nothing in the app calls it (its frontend client was deleted).
- **The ~79% hang.** Two causes, both fixed: (1) the panel never passed an
  `AbortSignal`, so a superseded "3D all" kept running server-side and
  starved everything behind it — every run now aborts its predecessor;
  (2) the progress number was fabricated (a timer creeping to
  `MESH_PROGRESS_START - 1`). Progress is now honest: an *indeterminate* bar
  plus elapsed seconds while the server meshes, a real per-label percentage
  while building, then 100% or a visible error. Aborted runs are silent, not
  reported as failures.
- **Decoupling (the app-wide part).** `label3DIds` now derives from
  `pinned3D` **only**. Solo / hide / Hide Verified feed a separate
  `hidden3DIds`, which toggles `mesh.visible` on already-loaded geometry —
  no refetch, no re-mesh. Previously solo narrowed `label3DIds` and
  `verifiedIds` fed into it, so every ○/◉ click and every Verify triggered a
  full 3D reload. The load effect also compares the pin set by sorted-id
  string, so an equal-but-new array can't retrigger it. Solo's tooltip was
  updated to describe what actually happens.

### C — Hot paths

- **Slice navigation** (`AnnotationCanvas`): a per-z image object-URL cache
  (small LRU, revoked on evict/unmount) + a label-RLE cache, and z±1/z+2
  prefetch 250 ms after the current slice settles. `getLabelIds` now receives
  the load's `AbortSignal` (it never did), and the staleness check happens
  *before* `img.src` is swapped, so a late response can't paint the wrong
  slice. Label caches drop per-z on Save and wholesale on any whole-volume
  write; image bytes can't change while the page is open, so they're kept.
- **Multi-user** (`slice_io`): `invalidate_read_caches()` cleared the decoded
  *and* encoded caches for **every file and every user** on each slice save —
  one annotator saving went cold for everybody, including the untouched image
  slices of the volume being edited (a label write cannot stale an image
  slice). It now takes the written path. `_save_label_volume` likewise used
  `clear_caches()` where all it needed was to release its own handle; new
  `slice_io.drop_file(path)` does exactly that. No locking changes were
  needed — writes were already per-slice memmap writes, not whole-volume
  rewrites.
- **SAM**: `EfficientSam._embed` keyed its in-process cache on
  `image_rgb.tobytes()` — a full copy **and** hash of a tens-of-MB slice on
  every predict, including each cursor-follow hover predict, just to look up
  a cache it was about to hit. It now keys on the embedding cache path
  (which already identifies volume/axis/index/variant/mtime) and defers
  `_to_rgb` until an encode actually happens (the decoder only needs the
  shape). Warm-on-z-nav was already gated on an AI tool being active, so pure
  navigation still does no SAM work.
- **Bounding boxes**: `label_bbox_3d` did `mask == lid` over the *whole*
  volume once per label, so "3D all" was O(labels x volume). Per-label boxes
  now ride along on the single cached `label_summary` scan
  (`scipy.ndimage.find_objects` per z-chunk). Measured on the dev heart
  volume (137x2758x2514, 52 labels): meshing all 52 takes ~0.2 s warm, ~0.7 s
  cold, and a repeat request is ~1 ms.

### D — Copy → Copied!!!

`shareHardCase` no longer auto-copies. The modal opens on **Copy**, and only
a user click whose `navigator.clipboard.writeText` **resolves** flips it to
**Copied!!!**; a rejected write says so in an `aria-live` line and the button
stays on Copy. (Auto-copying and opening on "Copied" was indistinguishable
from a silent failure.)

### Verification

- Backend: `manage.py test annotation accounts core projects volumes
  processing` → **237 tests, all passing** with the intended provider config.
  Run with this checkout's `.env` as-is (`MITO_VISUALIZATION_PROVIDER=
  placeholder`) the same suite reports 3 failures — they are pre-existing and
  purely that setting (`test_providers` + `RoleGatingApiTests` assert the
  `inapp` provider), not this work. New tests: mesh geometry
  unit tests (closed surface inside the label bbox, the wide-thin-label
  regression, per-label independence, absent labels), mesh API tests (binary
  round-trip parsed exactly as the frontend does, bad-list 400, access
  gating), the public share mesh endpoint (anonymous + bad token), and
  scoped-cache-invalidation tests.
- Frontend: `tsc --noEmit` clean, `npm run build` clean.
- End-to-end against the dev data (heart volume, 137x2758x2514, 52 labels)
  through a real server: created a share, then `GET …/meta/`,
  `GET …/slice/` (0.12 s cold, 0.007 s cached) and
  `POST …/labels-3d-mesh/` as an **anonymous** client — the mesh body parses
  byte-exactly with the frontend's decoder (4 labels, 658 780 bytes, offsets
  land exactly on the end of the buffer). First mesh request on a cold
  summary cache is ~9.4 s (the one-time whole-volume scan, down from ~21 s
  before the `bincount` + native-dtype `find_objects` change); subsequent
  requests are ~50 ms.
- **Not visually verified in a browser** — there is no headless Chrome on
  this node. The share page's layout fix is structural: it now renders the
  *same* `ViewerShell`/classes as the working authed route, with the viewport
  height supplied by `.full-bleed-standalone`.
- **Not pushed.**

### Known limits / follow-ups

- "3D all" on a densely annotated volume is capped by a server-side triangle
  budget (`DEFAULT_TRIANGLE_BUDGET`); labels past it are skipped and the
  panel says "N not shown". Per-label detail also drops as the pin count
  grows (`scaled_target`) — both are payload/WebGL guards, tune there.
- Mesh smoothing is a fixed `sigma=0.7` blur; there's no user-facing
  smoothing/decimation control.
- 3D never auto-refreshes after a paint/Save — it rebuilds on pin changes and
  the explicit 3D buttons only. That's the decoupling the brief asked for,
  but it does mean a user must re-click 3D to see edited geometry.
- Multi-writer contention is still "last write wins" per slice; nothing here
  added locking, and two annotators on the same task's same slice can still
  overwrite each other.
