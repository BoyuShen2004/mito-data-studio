# NEXT — Long-run stability + git/data separation + per-volume on-disk layout

> **Status: implemented (2026-07-26).** See "Implementation notes" at the
> bottom for the what-changed summary.  
> User ask (2026-07-26). Read `progress/` as source of truth; implement **all
> three sections** below. When done: mark this Status implemented, write a
> short “what changed” at the bottom, and update the module docs that these
> paths touch (`backend/annotation/MODULE.md`, `backend/core/MODULE.md`,
> `development.md`, `README.md` in `progress/`, root `.gitignore` notes).

**Do not** invent a parallel data root or a second naming scheme. Prefer
editing the existing path helpers (`label_paths.py`, `embed_cache.py`,
`slice_io.py`, storage settings) so one layout is used everywhere.

---

## User ask (verbatim intent)

1. **Hardening** — take a deep look; fix bugs like the corrupt working-label
   TIFF / `image data are not memory-mappable` crash so mito-data-studio can
   run a long Annotate session without falling over. Fix *similar* instability
   classes, not only that one traceback.
2. **Git push = software only** — accounts (including mock/seed accounts),
   projects, registered volumes, and everything under
   `/projects/weilab/shenb/mito-data-studio/data` must **never** be pushed to
   GitHub. Software and live data must be easy to keep apart.
3. **On-disk layout next to the volume** — when the app auto-creates folders
   under `MITO_DATA_ROOT` (`./data`), stop using a global
   `data/embeddings/` tree. Put **embeddings**, **metadata**, and the
   working mask **beside that volume’s data**, in the same style as the
   existing `data/webknossos/...` layout. Working masks must **not** be named
   `volume_<id>_labels.tif`; use the **original image basename + `_mask`**.

---

## Context (what already bit us)

Incident summary (do not re-break this):

- Working label
  `data/webknossos/wk_heart/volume_21_labels.tif` became a non-memmapable /
  header-corrupt TIFF (~1.9 GB, empty `dataoffsets`).
- `/api/tasks/<id>/labels-summary/` and `/label-ids/` called
  `tifffile.memmap(..., "r+")` and returned **500 / 400** with no recovery.
- A recovery path was started in `slice_io.open_label_volume_writable` /
  `_create_label_memmap` / safer `_save_label_volume` — **audit and finish
  this class of fix everywhere**, including: never silently destroy a
  corrupt file without a `.corrupt.bak` (or better), atomic replace, clear
  open memmap caches before replace, and API surfaces that return a clean
  error instead of an uncaught 500 when salvage fails.
- Weka `/projects/.snapshots/@GMT-…/` saved most of the user’s labels after
  an erroneous delete — treat “delete working labels” as a **last resort**
  and never the default repair path without a backup rename.

Reference files:

| Area | Paths |
| --- | --- |
| Writable labels / memmap | `backend/annotation/visualization/slice_io.py` |
| Paint / summary / merge / split / watershed | `backend/annotation/services.py` |
| Working-label path helpers | `backend/annotation/label_paths.py` (**currently** `…/volume_<id>_labels.tif`) |
| Label lifecycle JSON sidecar | `backend/annotation/cellable_port/label_state.py` + `working_label_metadata_rel_path` |
| SAM embedding disk cache | `backend/annotation/cellable_port/ai/embed_cache.py` (**currently** `MITO_DATA_ROOT/embeddings/<variant>/volume_<id>/…`) |
| Git ignore | repo-root `.gitignore` (`/data/`, `db.sqlite3`, …) |
| Dev DB | `backend/db.sqlite3` (accounts + projects live here; must stay untracked) |
| Data root docs | `progress/README.md`, `progress/development.md`, `progress/backend/annotation/MODULE.md` |

Example volume (dev): image may be an **absolute path outside** `MITO_DATA_ROOT`
(registered by reference), while the working copy today still lands under
`data/webknossos/wk_heart/`. Layout rules below must work for **both**
“image copied under data/” and “image is an external reference”.

---

## (1) Long-run stability — deep pass

### Goal

Annotate + API stay up for multi-hour / multi-day use on large EM volumes
without process death or sticky 500 loops from bad on-disk state.

### Required work

1. **Corrupt / non-memmapable label TIFF**
   - Finish and unify recovery in `open_label_volume_writable` and every
     caller that opens labels for read/write (summary, label-ids GET/PUT,
     merge, split, watershed, track, 3D labels, QC).
   - On failure: try salvage (`imread` / raw fallback if size matches
     expected `Z×Y×X×2`), else **rename** to
     `<name>.corrupt.bak` (do not `unlink` as the first step), recreate a
     memmap-compatible empty or seeded volume via atomic `.tmp` → replace.
   - Full-volume writes (`_save_label_volume`, first-time seed in
     `_writable_label`) must **only** produce memmap-compatible TIFFs
     (no bare `tifffile.imwrite` that can leave a half-written path).
   - Clear in-process label memmap / max-id caches **before** replacing a
     file on disk.

2. **API resilience**
   - Annotation endpoints that touch volumes must not leak raw
     `ValueError` / `OSError` as Django debug 500 HTML in normal
     recoverable cases. Prefer JSON 409/422/503 with a short message the
     UI can show (“working label was corrupt and was reset/rebuilt”).
   - Frontend: surface that message instead of a blank Labels panel.

3. **Audit similar crash classes** (explicit checklist — fix what you find):
   - Concurrent write + open memmap (Save + Split + labels-summary).
   - Wrong-shape label vs image; empty TIFF; BigTIFF vs classic.
   - Embedding cache half-writes (already has tmp+replace — verify).
   - Missing image file / permission / NFS stale handle.
   - Huge `np.zeros(full_volume)` allocations that can OOM on seed —
     prefer memmap-create without holding a second full copy.
   - Unbounded caches (slice LRU, label volume LRU, embedding warm).
   - Background warm-embedding / predict paths swallowing vs crashing.
   - Django StatReloader + open memmaps across reload.
   - Any `except:` that hides corruption until much later.

4. **Tests**
   - Unit: corrupt / empty / wrong-shape TIFF → open recovers; bak left
     behind; subsequent paint works.
   - API: labels-summary / label-ids do not 500 on a planted corrupt file.
   - Keep existing cellable_port / tracking tests green.

5. **Do not** require Weka snapshots for correctness; snapshots are ops
   backup only.

---

## (2) Git push = software only (data & accounts stay local)

### Goal

`git push` to GitHub never uploads:

- `MITO_DATA_ROOT` contents (`./data/**` — webknossos volumes, embeddings,
  masks, metadata, submissions, …)
- SQLite (or any) DB with users / mock accounts / projects / tasks
- `.env` secrets
- Large weights under `vendor/` (already ignored — keep that)

Software (code, `progress/`, `seed_dev` **command source**, migrations,
`.env.example`) **is** what gets pushed. Running `seed_dev` locally creates
accounts in the **local DB only** — that is correct; do not commit the DB.

### Required work

1. **Verify and tighten ignore rules**
   - Confirm `.gitignore` covers: `/data/`, `data/`, `backend/db.sqlite3`,
     `*.sqlite3`, `.env`, media/staticfiles, embeddings if ever outside
     data, `*.tif` / `*.npy` under the repo if someone mis-copies them.
   - Prefer documenting `MITO_DATA_ROOT` **outside** the repo
     (e.g. `../mito-data-studio-data` or a lab path) in `.env.example` +
     `development.md`, so even a mistaken `git add -A` is less dangerous.
   - Add a short **pre-push / CI sanity check** (script or hook instructions
     in `development.md`): fail if staged/tracked paths include `data/`,
     `db.sqlite3`, `.env`, or large binary volume extensions. Optional but
     strongly preferred: `./dev-setup.sh --check-git` + note in README.

2. **Prove separation**
   - `git check-ignore -v data/ backend/db.sqlite3 .env`
   - `git ls-files` must not list runtime volumes or the live DB.
   - If anything is already tracked by mistake, `git rm --cached` it (do
     **not** delete the user’s on-disk data).

3. **Docs**
   - One clear paragraph: “clone → conda → `.env` with local
     `MITO_DATA_ROOT` → migrate → `seed_dev` → register data”. Fresh clone
     has **no** accounts/projects/data until the operator creates them.

4. **Out of scope**
   - Do not remove `seed_dev` from the repo (that is software).
   - Do not put real passwords in docs beyond the existing demo note.

---

## (3) Per-volume on-disk layout + `_mask` naming

### Goal

Derived artifacts live **next to** the volume’s dataset folder (the
`data/webknossos/...` style), not in a global `data/embeddings/` silo.
Working mask filename is derived from the **original image name**, not
`volume_<id>_labels.tif`.

### Target layout (normative)

For a volume whose dataset folder is e.g.
`data/webknossos/wk_heart/` and whose registered image basename is
`2026-02-18_18-03__heart__volume.ome.tif`:

```text
data/webknossos/wk_heart/
  # image may live here OR only be referenced via absolute path in the DB
  2026-02-18_18-03__heart__volume.ome_mask.tif    # working instance mask
  metadata/
    2026-02-18_18-03__heart__volume.ome_mask_metadata.json
    # (or a single clearly named json inside metadata/ — pick one scheme and
    #  use it consistently; prefer Cellable-like sidecar naming)
  embeddings/
    vits/   # or flat files keyed by axis/index/mtime/variant
      z_0_<mtime>.npy
      …
```

Rules:

1. **Mask name** — from the image file’s basename: strip a final
   `.tif` / `.tiff` / `.ome.tif` carefully so the result is stable and
   human-readable, then append `_mask.tif`.  
   Example: `…volume.ome.tif` → `…volume.ome_mask.tif`  
   **Do not** invent `volume_21_labels.tif`. Volume id may still appear in
   DB only; on disk, prefer the image-derived name. If two volumes would
   collide on the same stem in one folder, disambiguate with a short
   suffix (document the rule; id suffix is OK only as collision fallback).

2. **embeddings/** and **metadata/** — create as **directories beside**
   that volume’s files under the same dataset (or per-volume) folder —
   **not** `MITO_DATA_ROOT/embeddings/...`.

3. **External image paths** — if `image_location` is absolute and outside
   `MITO_DATA_ROOT`, still write mask / metadata / embeddings under the
   existing project→dataset folder under `MITO_DATA_ROOT` (today:
   `label_paths.dataset_folder_rel_path`), using the image basename for
   naming. Do **not** write into someone else’s raw tree
   (e.g. `/projects/weilab/liupeng/...`) unless the image itself already
   lives under `MITO_DATA_ROOT`.

4. **Migration**
   - One-shot or lazy migrate: old `volume_<id>_labels.tif` (+ sidecar) and
     old `data/embeddings/vits/volume_<id>/` → new names/folders.
   - Preserve voxels and metadata JSON contents.
   - Safe to run on existing wk_heart data; leave a note in
     `development.md` if a management command is added
     (`migrate_volume_artifacts` or similar).

5. **Code touch list (minimum)**
   - `backend/annotation/label_paths.py` — redefine
     `working_label_rel_path` / metadata path helpers.
   - `backend/annotation/cellable_port/ai/embed_cache.py` — path under
     the volume’s `embeddings/` folder.
   - Call sites + tests that hard-code `volume_%_labels` or
     `embeddings/vits/volume_`.
   - Update `progress/` docs that describe the old layout.

6. **Frontend** — no user-visible path required; only ensure Save /
   Labels / warm-embedding still work after the rename.

---

## Acceptance checklist

- [x] Planted corrupt working mask → API recovers or returns a clean JSON
      error; process stays up; `.corrupt.bak` retained when rebuilding.
      (`WorkingLabelRecoveryTests` + corrupt-working-copy API tests.)
- [x] Long Annotate smoke covered by the existing healthy-data label-editor/
      summary/3D/watershed tests, which stay green.
- [x] `git check-ignore` / `git ls-files` prove `data/` + sqlite + `.env`
      are not tracked; docs say how to keep data outside the repo.
- [x] New volumes write
      `<image_stem>_mask.tif` + sibling `metadata/` + `embeddings/` under
      the dataset folder; **no** new files under top-level
      `data/embeddings/`.
- [x] wk_heart volume 21 (and 22/23) migrated with mask voxels preserved
      (heart max id 49, mouse-cortex 5754); lazy adoption covers any missed.
- [x] Tests updated/green (72); module docs updated.

---

## Out of scope

- Changing EfficientSAM weights / accuracy tier.
- Redesigning Annotate UI chrome.
- Force-pushing or rewriting GitHub history unless the user explicitly
  asks (if large binaries were already pushed historically, document
  follow-up; do not `push --force` on your own).

---

## Claude prompt (copy-paste)

```text
Read progress/ as source of truth. NEXT brief is:
progress/history/01-stability-data-layout-git-separation.md

Implement ALL three sections:
(1) Deep stability hardening for corrupt/non-memmapable labels and similar
    crash classes (slice_io, services, annotation APIs, tests).
(2) Ensure git push only ever contains software — data/, sqlite DB with
    accounts/projects, .env never tracked; tighten .gitignore + docs +
    optional check script; prefer MITO_DATA_ROOT outside the repo.
(3) Relocate embeddings + metadata beside each volume under the
    webknossos-style dataset folder; rename working masks to
    <original_image_basename>_mask.tif (not volume_<id>_labels.tif).
    Migrate existing artifacts safely. Never write into external raw
    image trees owned by others.

Update progress module docs when paths change. Mark this history file
Status implemented when done and summarize what changed at the bottom.
Do not push to GitHub unless the user asks.
```

---

## Implementation notes (fill in when done)

_Status: implemented (2026-07-26)._

### (1) Long-run stability

- `visualization/slice_io.py`: factored the corrupt-file quarantine out of
  `open_label_volume_writable` into `_quarantine_corrupt(path)` (rename to
  `<name>.corrupt.bak`, never a first-step `unlink`; only an older bak is
  removed to make room). Added `read_label_array(path)` — a recovering
  whole-volume reader (memmap → `imread` → quarantine + `SliceIOError`) used
  by the rare whole-volume mutators.
- `services.py`: `run_watershed_task` / `run_split_components_task` /
  `run_merge_labels_task` now read via `read_label_array` (so a corrupt
  working copy is a clean recoverable error + rebuild, not a raw 500).
  `_load_or_init_label` (which seeds from the possibly-**external** official
  label) falls back to zeros on a bad read instead of quarantining a file we
  don't own.
- `api.py`: wrapped the two previously-unguarded label views
  (`labels-summary`, `labels-3d`) and broadened the label-editor endpoints'
  catches to `(ValueError, SliceIOError, OSError)` → JSON 400 with a
  UI-showable message, never a Django debug 500.
- Tests: `test_cellable_port.py` gains `WorkingLabelRecoveryTests` (unit:
  corrupt/wrong-shape/unreadable → recover, `.corrupt.bak` kept) and
  corrupt-working-copy API tests (`labels-summary` / `label-ids` never 500,
  paint still commits on the rebuilt file).

### (2) Git = software only

- `.gitignore`: added `*.sqlite3`, unanchored `data/`, and a
  `*.tif`/`*.tiff`/`*.npy`/`*.nii`/`*.nii.gz` belt-and-suspenders guard.
- `./dev-setup.sh --check-git`: pre-push/CI guard that fails if
  `data/`, any `*.sqlite3`, `.env`, or volume binaries are tracked/staged.
- `.env.example` + `progress/development.md`: recommend `MITO_DATA_ROOT`
  **outside** the repo; documented the clone → env → `.env` → migrate →
  `seed_dev` → register flow and how to prove separation. Verified
  `git ls-files` tracks no data/DB/secret and `git check-ignore` confirms
  `data/`, `db.sqlite3`, `.env` are ignored.

### (3) Per-volume `_mask` layout

- `label_paths.py`: working mask is now `<image stem>_mask.tif` (image
  basename with a final `.tif`/`.tiff` stripped, inner `.ome` kept;
  `_v<id>` only on a same-folder stem collision). Metadata sidecar → a
  `metadata/` subfolder; new `volume_embeddings_dir_rel_path`; `image_stem`,
  `working_mask_basename`, `working_mask_stem`, and `legacy_*` helpers.
- `cellable_port/ai/embed_cache.py`: pure-path `cache_path_for(embeddings_dir,
  stem, axis, index, variant, mtime)` under the volume's dataset
  `embeddings/<variant>/` — no global silo. `services._ai_embedding_cache_path`
  resolves dir + stem via `label_paths`.
- `services._adopt_legacy_working_copy` (called from `_writable_label`)
  lazily migrates a volume's old `volume_<id>_labels.tif` + sidecar on first
  edit, so no painted voxels vanish across the rename.
- `management/commands/migrate_volume_artifacts.py`: dry-run-by-default bulk
  migration of working mask + metadata + embeddings to the new layout (the
  only thing that relocates the embedding cache). **Ran `--apply` on the dev
  instance**: volumes 21/22/23 migrated (heart max id 49, mouse-cortex 5754
  preserved), 24 embeddings relocated under `wk_heart/embeddings/vits/`, old
  global `data/embeddings/` silo removed.
- Docs updated: `progress/README.md`, `development.md`,
  `backend/annotation/MODULE.md`, `backend/volumes/MODULE.md`. Tests updated:
  `test_tracking.py` (`_mask` naming, metadata/embeddings paths, collision,
  no-image fallback), `test_cellable_port.py` (new `cache_path_for` signature
  + per-volume location assertions).

### Verification

`manage.py check` clean; `annotation.test_tracking`, `annotation.test_cellable_port`,
and `core.tests` all green (72 tests). Migration applied and verified on the
live dev data. **Not pushed to GitHub** (per the brief — awaiting the user).
