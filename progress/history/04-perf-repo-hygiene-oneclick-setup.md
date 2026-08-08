# NEXT — Perf + repo hygiene + one-click remote setup

> **Status: implemented (2026-07-27).** See "Implementation notes" at the
> bottom.  
> User ask (2026-07-27): (1) make the app **fast / smooth / stable** everywhere
> that still feels slow (Labels → All, voxel/size/layer metadata, and similar);
> (2) simplify the **GitHub-facing tree** — merge/clear redundant files, folders,
> and code so outsiders can read the repo; (3) make **conda + setup truly
> one-shot** on a different server so the app opens and runs.
>
> Builds on work already done in
> [`01-stability-data-layout-git-separation.md`](01-stability-data-layout-git-separation.md),
> [`02-share-hard-case.md`](02-share-hard-case.md),
> [`03-fix-hard-case-share-view.md`](03-fix-hard-case-share-view.md)
> (slice/SAM/3D hot paths partially optimized — do **not** redo those blindly;
> measure, then extend).

When done: mark Status implemented, update module docs (`progress/development.md`,
`progress/README.md`, relevant MODULE.md / `api.md`), append notes below.
**Do not push** unless the user asks. Prefer measurable wins over speculative
micro-refactors. Keep features modular (same View / Annotate / share codebase).

---

## Goals (user wording → acceptance)

| # | Goal | Done when |
| --- | --- | --- |
| **A** | App feels **fluent, fast, stable** | Labels **All**, size/layer/metadata, slice nav, summary, 3D pin, SAM paths: no unexplained freezes; cold→warm clearly improved where profiled; multi-user saves don’t thrash others |
| **B** | Repo **simple & clear on GitHub** | Top-level layout readable in ≤10 entries of intent; no duplicate install paths / dead trees / orphan docs; redundant code merged or deleted with tests still green |
| **C** | **One-click setup on another server** | Fresh machine: clone → LFS → `conda env create` → `./dev-setup.sh` → `./dev-launch.sh` → UI up; README matches reality; failed steps print actionable errors |

---

## A — Performance & stability

### Known slow / sticky UX (start here)

1. **Labels panel → “All”**  
   Backed by `GET …/labels-summary/` → `label_summary()` in
   `backend/annotation/cellable_port/labels_3d.py`: whole-volume memmap scan
   (chunked z + `find_objects`), cached by **mtime**. Cold open on large
   volumes (e.g. heart ~137×2.7k×2.5k) is the pain; warm should be near-instant.
   Also check frontend: `AnnotationCanvas` refresh tokens, list virtualization,
   unnecessary re-fetches on tab switch / solo / hide.

2. **Size / layer / shape / voxel metadata**  
   Volume registration / detail pages / meta endpoints that call
   `inspect_volume_shape` / `inspect_volume_voxel_size` or touch large TIFFs
   without caching. User reports “load size and layers info” slightly slow —
   find every place that re-opens headers or scans disks and cache or batch.

3. **Similar classes of problem (hunt & fix)**  
   - First paint of Annotate/View (meta + label-state + first slice + summary)  
   - Labels list filters / “This slice” vs “All” thrashing  
   - 3D pin / “3D all” still expensive (budget already exists — ensure abort +
     per-label cache stay effective)  
   - Slice nav / SAM warm (partially done in #03 — verify no regression; extend
     if still cold)  
   - Save / labels-summary invalidation too broad  
   - Large React trees (`AnnotationCanvas.tsx` ~3k lines) causing avoidable
     re-renders on unrelated state  

### Method (required)

1. **Profile before rewriting.** Note wall-clock for: cold `labels-summary`,
   warm `labels-summary`, Labels All UI, volume meta, one z-step, one SAM click
   (cache hit vs miss). Use Django timing / `time` / browser Network + React
   profiler as needed.
2. Prefer: **cache**, **incremental / incremental invalidation**, **abort
   superseded work**, **don’t couple unrelated UI**, **lazy load**.
3. Avoid: loading whole EM volumes into RAM; silent delete of user data;
   parallel “fast path” forks of View vs Annotate.
4. Stability: no uncaught 500s on corrupt TIFF (existing recovery); timeouts /
   AbortSignal where long POSTs run; don’t leave orphan GPU/CPU jobs.

### Concrete starting points

| Area | Paths |
| --- | --- |
| Labels All / summary | `cellable_port/labels_3d.py` (`label_summary`), `services.get_labels_summary`, `LabelsPanel.tsx`, `AnnotationCanvas` summary token |
| Volume meta / shape | `core/utils.py`, `volumes/services.py`, volume/task serializers & detail pages |
| Slice / SAM | `slice_io.py`, `AnnotationCanvas` caches, EfficientSAM embed cache |
| API surface | `annotation/api.py`, `progress/api.md` |

### Acceptance (A)

- [x] Cold Labels **All** / `labels-summary` meaningfully faster **or** UI shows
      honest progressive/cached state (never silent multi-second freeze with no
      feedback).
- [x] Warm summary / All switch is snappy (cache hit).
- [x] Size / layer / voxel metadata loads don’t re-scan needlessly.
- [x] At least 2–3 other measured hotspots fixed (document before/after).
- [x] No regression on slice nav / Track / share View.
- [x] Tests for summary cache invalidation and any new incremental path.

---

## B — Repo hygiene (GitHub-readable)

### Intent

Someone opening `github.com/.../mito-data-agent` should understand in one
screen: what it is, how to install, where code lives. No duplicate “how to
run” stories, no dead install files, no mystery folders.

### Audit & act

1. **Top-level inventory** — keep a small set:
   `README.md`, `environment.yml`, `dev-setup.sh`, `dev-launch.sh`,
   `.env.example`, `backend/`, `frontend/`, `vendor/`, `progress/`,
   `.gitattributes`, `.gitignore`, `pyproject.toml` (pytest only — OK).
2. **Merge or delete redundancy**
   - Duplicate install/run docs: keep **README** as the front door; make
     `progress/development.md` the deep dive — **no conflicting commands**.
   - Dead trees: unused scripts, leftover `requirements*.txt`, empty stubs,
     commented-out vendors, obsolete Cellable copies **inside this repo**
     (do **not** delete `/projects/weilab/shenb/cellable` — that is a sibling
     project).
   - `.claude/` — local settings already gitignored; ensure nothing private
     is tracked.
   - `frontend/dist/` — must stay gitignored (build artifact).
   - `data/`, sqlite, `.env` — never tracked (`./dev-setup.sh --check-git`).
3. **Code consolidation** (only where it reduces confusion)
   - Duplicate helpers / near-copy View vs share paths → already prefer
     `ViewerReadApi` / shared shell; finish any remaining forks.
   - Giant files: if you split `AnnotationCanvas.tsx`, do it **only** along
     clear module boundaries (chrome / slice load / paint) with no behavior
     change — optional, not required if time is spent on A/C.
4. **Docs**
   - `progress/codemap.md` / MODULE.md updated after moves.
   - History briefs stay; don’t duplicate their full text into README.

### Acceptance (B)

- [x] Top-level listing is intentional and documented in README “Layout”.
- [x] One canonical install path (matches §C); no second competing
      `requirements.txt` / alternate setup script unless justified in README.
- [x] `./dev-setup.sh --check-git` clean; no `data/` / sqlite / `.env` in git.
- [x] Removed/merged items listed in Implementation notes.
- [x] Tests + `npm run build` still pass.

---

## C — One-click setup on another server

### Target story (must work)

```bash
git clone <repo> && cd mito-data-agent
git lfs install && git lfs pull
conda env create -f environment.yml
conda activate mito-data-agent
./dev-setup.sh
./dev-launch.sh
# → http://localhost:5173  (or documented host/port)
```

Optional documented variants only: CPU-only (no `pytorch-cuda`),
`--install-deps`, mature-env safe path.

### Harden

1. **`environment.yml`** — pins that actually resolve on a clean conda;
   note CUDA line; fail clearly if LFS pointers left as tiny pointer files.
2. **`dev-setup.sh`** — check: python/node/npm, critical imports, vendor
   weights present (not LFS stubs), `.env` from example if missing, npm when
   needed, `migrate`. Default still **no** surprise `conda env update`.
3. **`dev-launch.sh`** — both servers; clear URL; bind/host notes for remote
   SSH tunnels if already present — don’t invent new deploy systems.
4. **README** — identical recipe to scripts; “fresh vs mature” table accurate.
5. **Smoke** — document or script a minimal “setup worked” check
   (e.g. `manage.py check`, frontend build, import torch/onnxruntime once).

### Out of scope for C

- Docker / k8s / production WSGI deploy  
- Auto-downloading datasets into `data/`  
- Force-pushing LFS without quota  

### Acceptance (C)

- [x] README + `environment.yml` + `dev-setup.sh` + `dev-launch.sh` tell one story.
- [x] Missing LFS / missing CUDA / missing Node fail with a **fixable** message.
- [x] Fresh-env path exercised or carefully dry-run reviewed end-to-end.
- [x] `seed_dev` / first-login still documented.

---

## Constraints

- **Do not push** unless asked.  
- **Do not** commit `data/`, sqlite, `.env`, or secrets.  
- **Do not** delete user annotation volumes or corrupt backups.  
- Prefer modular reuse; no share-only or annotate-only performance forks.  
- If a change is risky (summary format, cache keys), add tests first.

---

## Suggested work order

1. Profile Labels All + meta (A) — quick wins + honest loading.  
2. Repo audit + safe deletes/merges (B).  
3. Setup path walkthrough + script/README fixes (C).  
4. Re-measure A; fill Implementation notes; update docs.

---

## Claude prompt (copy-paste)

```text
Read progress/ as source of truth. NEXT brief is:
progress/history/04-perf-repo-hygiene-oneclick-setup.md

Do A–C:

A) Optimize for fluent/fast/stable UX — especially Labels → All
   (labels-summary), size/layer/metadata loads, and similar hotspots.
   Profile first; cache/abort/decouple; don’t fork View vs Annotate.

B) Simplify the GitHub-facing tree: merge/clear redundant files, folders,
   and code; one clear layout; keep data/sqlite/.env out of git.

C) Make conda + ./dev-setup.sh + ./dev-launch.sh a true one-shot on a
   fresh server; README must match; fail with actionable errors (LFS, CUDA,
   Node, vendor weights).

Update docs. Do not push.
```

---

## Implementation notes

_Status: implemented (2026-07-27). Not pushed._

### A — Performance & stability

**Profiled first** (dev data, this node: 3 volumes — heart 137x2758x2514,
liver 160x3885x4544, mouse_cortex 256x2048x2048 with 5,754 labels).

| Path | Before | After | How |
| --- | --- | --- | --- |
| `labels-summary` cold, heart | 9.9s | **1.1s** | per-slice stats + threaded scan |
| `labels-summary` cold, liver | 27.4s | **0.4s** | ditto (empty volume exits early) |
| `labels-summary` cold, mouse_cortex (5,754 labels) | 11.1s | **3.6s** | ditto |
| `labels-summary` **right after a Save** | full rescan (10-27s) | **17ms** | incremental fold-in |
| `labels-summary` warm (HTTP, 5,754 labels, 747KB) | — | **29-38ms** | cache hit |
| `inspect_volume_shape` repeat | 7-200ms | **0.05ms** | (path, mtime) cache |

- **The summary is now per-slice statistics, summed** (`cellable_port/labels_3d.py`).
  The cache holds `{label: {z: (count, y1, y2, x1, x2)}}`; rows and 3D crop
  boxes roll up from it. That buys two things:
  - the whole-volume scan runs **per z-slice across threads** (`_scan_stats`;
    numpy/scipy release the GIL). Per-slice `find_objects` is also cheaper than
    the whole-chunk call it replaced — 9.3s → 2.3s single-threaded before
    threading took it to ~1.1s.
  - **a Save folds into the cache instead of invalidating it**
    (`update_summary_for_slice`, called from `set_label_slice_ids`). This was
    the big one: the `labels-summary` request that follows *every Save* used to
    rescan the volume. It refuses the fold-in when the file's mtime doesn't
    match what the cache was built from (another writer got in first) and lets
    the next read rescan — that is the multi-writer safety property.
  - Whole-volume writers (watershed / split / merge / tracking / lifecycle
    reject) call `forget_summary`; there's no single slice to fold in.
  - Exactness is asserted against a brute-force pass, both in the unit tests
    and once against the real 52-label heart volume.
- **Labels "All" list is windowed** above 200 rows (`useVirtualRows.ts`):
  mouse_cortex renders ~30 rows instead of 5,754 (~35k DOM nodes), and the
  filter box no longer re-renders all of them per keystroke. Short lists and
  "This slice" are untouched. `focusId` still reaches rows outside the window
  (scroll by index).
- **Volume metadata** (`core/utils.py`): `inspect_volume_shape` /
  `inspect_volume_voxel_size` are cached by (path, mtime). The 3D mesh path
  calls the latter on every request when the DB has no voxel size.
- **Voxel size was also wrong, not just slow.** It filled axes from whichever
  source had them — OME-XML for z, TIFF resolution tags for x/y — with no unit
  normalisation. On the real files here (`PhysicalSize*` in nm *and* an
  `XResolution` rational decoding to ~10^6 µm) that produced a voxel 30,000x
  wider than deep, which the new 3D view would draw as a flat sheet. Now:
  everything in µm, OME taken as a whole when present, `ResolutionUnit = none`
  treated as "no physical size", and `_render_voxel_size` rejects any triple
  with >50:1 anisotropy. mouse_cortex now resolves to (0.028, 0.01124,
  0.01124) µm — a real 2.5:1 acquisition — instead of (28, 889595, 889595).
  Found while profiling; fixing it was a prerequisite for trusting the 3D view
  on that volume.
- **Slice caches are keyed `axis:index`** now that the Axial/Coronal/Sagittal
  selector exists. `changeAxis` did clear them, but a fetch already in flight
  could still land after the switch and insert the old plane under the new
  axis's index. Scoped keys make that impossible — and let both caches survive
  an axis switch, so switching back is instant.
- Prefetch cancellation moved to its own effect keyed on `axis`: it was
  rolling the shared AbortController on every *index* change, which can cancel
  a foreground load that de-duped onto a prefetch promise.

### B — Repo hygiene

The tree was already close to the target; the audit found little dead weight
and a fair amount of *duplicated instructions*, which is what got cut.

- **Verified clean**: 292 tracked files; top level is exactly the 11 intended
  entries; no tracked `__pycache__`/`.pyc`/`dist/`/`node_modules`; no
  `requirements*.txt`; nothing tracked under `.claude/`; `./dev-setup.sh
  --check-git` passes. A scan for unimported modules found **no** dead code in
  either `frontend/src` (only `vite-env.d.ts`, which is a type declaration) or
  `backend/` (only test files and management commands, both loaded by name).
  Nothing was deleted because nothing was dead.
- **Merged the duplicate install story**: `progress/development.md` carried a
  second (and a third) copy of the README's recipe — "Fresh clone", "mature
  env", "First-time environment". It now points at the README for installing
  and keeps only the deep-dive half: day-to-day loop, what the scripts will and
  won't do to your env, the flags table, CUDA/CPU-only notes, running the two
  processes by hand.
- **README**: the "Layout" block now lists every top-level entry with intent
  (it was missing `environment.yml`, `.env.example`, `pyproject.toml`) plus
  what is deliberately not in git; added `--smoke`; the test command says to
  run it from `backend/`.
- **`pyproject.toml`** kept but made honest: it is pytest config only, pytest
  is *not* in `environment.yml`, and Django's runner is the supported path.
  (Previously it read as if `pytest` just worked, which it doesn't in the
  documented env.)

### C — One-shot setup on another server

- **Fixed a real fresh-install trap**: `.env.example` shipped
  `MITO_VISUALIZATION_PROVIDER=placeholder` and
  `MITO_PROOFREADING_PROVIDER=placeholder`, while the code defaults to `inapp`
  and the whole viewer/editor *is* the in-app one. A fresh clone therefore came
  up with the task page's Annotation card saying "no provider configured"
  (direct `/viewer/...` links still worked, which makes it more confusing, not
  less) — and it is why `test_providers` / `RoleGatingApiTests` failed on this
  checkout. Both now default to `inapp`, with a comment saying when to change
  them.
- **`dev-setup.sh`** — actionable failures for the cases the brief names:
  - LFS: distinguishes "weights missing" from "weights are pointer files", and
    if `git-lfs` itself isn't installed says so with install commands for
    conda/apt/brew instead of suggesting a command that will fail.
  - CUDA: `torch` reporting `cuda=False` is now a warning that says what it
    costs (Track on CPU), how to check (`nvidia-smi`), how to fix (match
    `pytorch-cuda=` in `environment.yml`, re-create) and the CPU-only escape
    (`MITO_TRACKING_PROVIDER=local`).
  - `.env`: warns (never rewrites) when a pre-existing `.env` has the
    providers set to `placeholder`; creates `MITO_DATA_ROOT` if missing and
    fails early if it isn't writable.
  - New **`--smoke`**: loads onnxruntime *through the app's own session
    options*, opens the real EfficientSAM encoder file, imports torch, and
    builds the frontend. Proves the install works rather than that the checks
    passed.
- **Verified on this machine**: `./dev-setup.sh --smoke` runs clean end to end
  (and the `.env` provider warning correctly fires on this checkout's own
  `.env`, which still has `placeholder` — deliberately not edited, since it is
  the user's local file).
- README / `environment.yml` / `dev-setup.sh` / `dev-launch.sh` now tell one
  story; `seed_dev` + first-login is still documented in the README's "After
  first launch" and `development.md`.

### Verification

- Backend: `cd backend && python manage.py test` → **248 tests, all passing**
  (the 3 provider failures that used to appear on this checkout are gone for a
  fresh install now that `.env.example` says `inapp`; with this checkout's own
  `.env` they still fail, which is exactly what the new setup warning tells the
  user to fix). New tests: summary exactness vs a brute-force pass;
  incremental fold-in for grow / shrink / erase-entirely / stale-mtime /
  no-cache; an end-to-end "saving a slice does not rescan the volume" test
  (patches `_scan_stats` to raise); OME nm-unit normalisation; and
  `ResolutionUnit = none`.
- Frontend: `tsc --noEmit` clean, `npm run build` clean.
- End-to-end over HTTP against the dev data, numbers in the table above.
- `./dev-setup.sh --check-git` clean.
- **Not pushed.**

### Known limits / follow-ups

- The first `labels-summary` per volume *per server process* still scans the
  volume (1-3.6s here). Persisting the per-slice statistics beside the mask
  would remove even that; deliberately not done in this round — it adds a
  cache format on disk, and the fold-in already removed the repeated cost.
- `update_summary_for_slice` is in-process. Two Django worker processes each
  keep their own cache; correctness holds (the mtime check refuses a fold-in
  built on someone else's write and rescans), but the losing process pays for
  a rescan. A shared cache would need Redis/memcached, which this deployment
  doesn't have.
- Volumes registered before the voxel-size fix keep their stored (possibly
  bogus) values — the autodetect only fills blanks. `_render_voxel_size`'s
  50:1 guard keeps 3D sane meanwhile; there is no UI to edit voxel size, so
  correcting a stored value means re-registering the volume.
- Windowed Labels rows must stay single-line; a future row redesign that wraps
  would need the hook to measure per-row heights.
- `AnnotationCanvas.tsx` is still ~3.1k lines. Splitting it was listed as
  optional in the brief and skipped in favour of A/C work; the seams are the
  ones the file already comments (chrome / slice load / paint / layout).
