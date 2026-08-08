# Development

## Run it

**Installing is documented once, in the [README](../README.md#install-pick-one-path)**
— clone, `git lfs pull`, `conda env create -f environment.yml`,
`./dev-setup.sh`, `./dev-launch.sh`. Don't duplicate that recipe here; this
file covers what the README deliberately leaves out.

### Day to day

```bash
conda activate mito-data-agent
./dev-launch.sh                     # both servers; Ctrl+C stops both
```

Then open **http://localhost:5173**. Visiting **http://127.0.0.1:8000/** hits
the API server (a small landing page), not the app.

Ordinary code changes (React, CSS, Django views/serializers/services/tests)
need no setup step — save and the dev servers reload. Re-run `./dev-setup.sh`
after pulling **migrations** or a change to `frontend/package*.json`; it
re-runs `npm` only when those lockfiles changed or `node_modules` is missing,
and `migrate` is the only DB write it performs.

### What the scripts will and won't do to your environment

`./dev-setup.sh` is safe on a mature env by default: **no** `conda install`,
`conda env update`, or `pip install`, and it never overwrites an existing
`.env`. It checks tools/imports/vendor weights, creates `.env` from
`.env.example` only if missing, ensures `MITO_DATA_ROOT` exists and is
writable, warns when `.env` disables the in-app viewer
(`MITO_VISUALIZATION_PROVIDER=placeholder`), then runs `check` + `migrate`.

| Flag | Adds |
| --- | --- |
| `--install-deps` | pip-installs missing *light* packages (hydra, onnxruntime, …). Never touches conda/pytorch. |
| `--smoke` | Proves the install works: loads onnxruntime + a real vendor weight file, imports torch, builds the frontend (~1 min). Worth running once on a new server. |
| `--check-git` | Fails if `data/` / sqlite / `.env` / volume binaries are tracked or staged. |

To deliberately sync an old env with `environment.yml` (can upgrade packages,
including pytorch): `conda env update -f environment.yml --prune`. The file
pins `mkl<2024` so conda-forge numpy/scipy updates do not break the CUDA
PyTorch build — see [Conda env / PyTorch / MKL](#conda-env--pytorch--mkl).

`environment.yml` is the **single** install path (conda + all pip deps
inline) — there are no `requirements-*.txt` files. If `pytorch-cuda=12.4`
doesn't match your driver, edit that line *before* creating the env
(`nvidia-smi` shows the driver's CUDA version); on a CPU-only machine drop the
line and set `MITO_TRACKING_PROVIDER=local` in `.env`.

### Conda env / PyTorch / MKL

PyTorch for SAM2 Track comes from conda (`pytorch` + `pytorch-cuda` in
`environment.yml`), not pip. Two conda-forge pitfalls show up as torch
warnings in `./dev-setup.sh` even when `conda list pytorch` looks fine:

1. **MKL 2025** — `import torch` fails with
   `libtorch_cpu.so: undefined symbol: iJIT_NotifyEvent`. Common after
   `conda install numpy scipy` or `conda env update` without the repo's MKL
   pin.
2. **MKL 2024.2 from conda-forge** — solver may install a **CPU-only**
   `pytorch` from conda-forge instead of the CUDA build from the `pytorch`
   channel (`llvm-openmp` version conflict).

`environment.yml` guards against both:

- explicit **`mkl<2024`** dependency (resolves to `mkl 2023.1.x` from
  `defaults`)
- channel order: **`pytorch` → `nvidia` → `defaults` → `conda-forge`**

After any intentional env reshape (`conda env update`, manual `mkl`/`pytorch`
install), verify:

```bash
conda activate mito-data-agent
python -c "import torch; print(torch.__version__, 'cuda=', torch.cuda.is_available())"
conda list mkl pytorch | grep -E '^(mkl|pytorch) '
./dev-setup.sh --smoke
```

Healthy output: `pytorch` build string contains `cuda` (when using GPU) and
`mkl` is `2023.1.0` (or another version < 2024), not `2025.x`.

If torch is still broken and you cannot re-sync from `environment.yml`, the
minimal repair is:

```bash
conda install pytorch=2.5.1 torchvision=0.20.1 pytorch-cuda=12.4 "mkl<2024" \
  -c pytorch -c nvidia -c defaults -c conda-forge
```

(Adjust `pytorch-cuda=` for your driver, or omit it for CPU-only.)

### Run the two processes by hand (debugging)

```bash
python backend/manage.py runserver          # API on http://127.0.0.1:8000
npm run dev --prefix frontend               # UI on http://localhost:5173
```

## Configuration (`.env`)

`.env` at the repo root (copied from `.env.example`) drives the backend:

- `MITO_DATA_ROOT` — root dir for all volume/label/submission files. The DB
  stores only paths relative to this root, **never** the image data. A relative
  value resolves against the repo root (so `./data` means `<repo>/data`).
  **Prefer a path outside the repo** (e.g. `../mito-data-agent-data` or a lab
  path) — see "Keeping software and data apart" below. Per-volume working
  artifacts live under `<project>/<dataset>/`: `<image stem>_mask.tif`, a
  `metadata/` sidecar, and an `embeddings/<variant>/` SAM cache (see
  `backend/annotation/label_paths.py`).
- `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` — standard Django.
- `DJANGO_CORS_ORIGINS` — browser origins allowed to call the API.

Everything else is in `backend/config/settings.py`.

## Dev data & accounts

Standard accounts (password `demo12345`): `manager` (manager); `alice`,
`bob`, `carol`, `dave` (annotators); `requester1`, `requester2` (requesters —
the "customers" the manager's People view is about; they sign in through the
**Requester** login tab). Each gets a display name/institution so the People
roster isn't a row of blanks (`core.dev_data.STANDARD_PROFILES`). **No data is
pre-registered** — register datasets yourself in the app.

```bash
cd backend
python manage.py seed_dev            # create the standard accounts (no data)
python manage.py seed_dev --fresh    # clear existing data first, then seed
python manage.py dev_status          # counts of current data
python manage.py clear_dev_data      # delete dev data (prompts; --no-input to skip)
python manage.py clear_dev_data --keep-users
python manage.py reset_dev           # clear + migrate + reseed accounts (one shot)
```

`clear_dev_data` / `reset_dev` always preserve superusers and refuse to run when
`DEBUG=False` unless given `--force`. Both also wipe **everything under
`MITO_DATA_ROOT`** (registered uploads, submission files, and every
project/dataset's in-app working label copy — not just database rows). Automated tests build
their own throwaway data (in a tempdir `MITO_DATA_ROOT` override) and are
independent of these commands. In development the login page shows a
click-to-fill list of these accounts (dev builds only), plus a "Clear all
data & reset" button that calls the same `clear_dev_data` through
`POST /api/dev/reset/`.

Create the Manager Admin superuser with `python manage.py createsuperuser` (see
[admin.md](admin.md) for how manager accounts get admin access).

## Keeping software and data apart (git push = software only)

`git push` must only ever carry **software** — code, `progress/`, migrations,
management commands (`seed_dev` included), `.env.example`. It must **never**
carry runtime data:

- the SQLite dev DB (`backend/db.sqlite3`) — it holds users (including
  mock/seed accounts), projects, datasets, volumes, tasks;
- anything under `MITO_DATA_ROOT` (`data/**` — volumes, working masks,
  embeddings, metadata, submissions);
- `.env` secrets; large weights under `vendor/`.

`.gitignore` covers all of these (`*.sqlite3`, `/data/` + `data/`, `.env`,
`vendor/*`, and a belt-and-suspenders `*.tif`/`*.npy`/`*.nii` guard for any
mis-copied volume binary). Two ways to stay safe:

1. **Keep `MITO_DATA_ROOT` outside the repo** (`.env`) so even a mistaken
   `git add -A` can't stage gigabytes of volumes. The in-repo `./data`
   default works and is gitignored, but the outside-the-repo path is safer.
2. **Run the guard** before pushing — fails if data/DB/secrets/volume
   binaries are tracked or staged:

   ```bash
   ./dev-setup.sh --check-git
   # optional pre-push hook:
   printf '%s\n' '#!/bin/sh' 'exec "$(dirname "$0")/../../dev-setup.sh" --check-git' \
     > .git/hooks/pre-push && chmod +x .git/hooks/pre-push
   ```

Prove separation any time: `git check-ignore -v data/ backend/db.sqlite3 .env`
and `git ls-files | grep -E '(^|/)data/|\.sqlite3|\.env$'` (should be empty).
A fresh clone therefore has **no** accounts, projects, or data: clone → create
the conda env → copy `.env.example` to `.env` and point `MITO_DATA_ROOT` at a
local (ideally outside-repo) dir → `migrate` → `seed_dev` → register data in
the app. If something *is* already tracked by mistake, `git rm --cached` it
(never delete the on-disk data).

## Migrating on-disk label artifacts

When the per-volume on-disk layout changes, two commands relocate existing
files without losing voxels (both dry-run by default, `--apply` to act):

- `python manage.py migrate_volume_artifacts` — moves each volume's *working*
  files from the old `volume_<id>_labels.tif` + global `data/embeddings/`
  scheme to `<image stem>_mask.tif` + sibling `metadata/` + `embeddings/`
  under the dataset folder. The editor also adopts a legacy working copy
  lazily on first touch, so this is a bulk cleanup (and the only thing that
  relocates the embedding cache), not a correctness prerequisite.
- `python manage.py reorganize_labels` — the sibling command for the
  *official*, DB-recorded `label_path` (moves it to wherever
  `label_paths.working_label_rel_path` currently points).

## Workflow CLIs

```bash
python backend/manage.py assign_tasks --project-id 1
python backend/manage.py progress_report --project-id 1
```

These are thin wrappers over the service layer (same logic as the SPA/admin).

## Tests & build

```bash
cd backend && python manage.py test        # backend — must run *from* backend/
python backend/manage.py check             # system checks
npm run build --prefix frontend            # frontend typecheck (tsc) + build
./dev-setup.sh --check-git                 # no data/ / sqlite / .env tracked
```

Django discovers tests relative to the working directory: run the suite from
`backend/`, not the repo root (from the root it finds nothing and cheerfully
reports "Ran 0 tests"). `pyproject.toml` carries pytest settings for anyone
who prefers `pytest`, but pytest is **not** part of `environment.yml` — the
Django runner above is the supported path.

See [codemap.md](codemap.md#where-the-tests-live) for which tests cover what.

## Providers & processing jobs

Replaceable integrations are chosen by env/settings (see
[codemap.md](codemap.md#one-replaceable-feature--one-folder) for the folders):

```bash
MITO_QC_PROVIDER=basic                 # annotation/quality_control/
MITO_PROOFREADING_PROVIDER=inapp       # annotation/proofreading/
MITO_VISUALIZATION_PROVIDER=inapp      # annotation/visualization/
MITO_PUBLISHING_PROVIDER=placeholder   # annotation/publishing/
MITO_PROCESSING_BACKEND=local          # processing/adapters/{local,slurm}.py
```

Heavy work runs as `ProcessingJob` rows, never inside a request. Run the
dispatcher to execute queued jobs:

```bash
cd backend
python manage.py run_processing_dispatcher --once     # single pass (local backend by default)
python manage.py run_processing_dispatcher            # loop
```

**SLURM** (`MITO_PROCESSING_BACKEND=slurm`) reads all cluster-specific values
from the environment — nothing is hard-coded:

```bash
MITO_SHARED_STORAGE_ROOT=/shared/mito
MITO_SLURM_PARTITION=gpu
MITO_SLURM_ACCOUNT=weilab
MITO_SLURM_SBATCH=sbatch   # + MITO_SLURM_SQUEUE / SACCT / SCANCEL
```

No real cluster is needed for local development or tests (the `local` backend
simulates jobs). The per-job command/script goes in `job.config['command']`.

## Remote / HPC access

The servers bind to localhost by default. Forward the port over SSH:

```bash
ssh -L 5173:localhost:5173 <username>@<server>   # then open http://localhost:5173
```

…or bind to all interfaces and skip auto-opening a browser:

```bash
VITE_HOST=0.0.0.0 DJANGO_HOST=0.0.0.0 NO_BROWSER=1 ./dev-launch.sh
```

Overrides (defaults): `DJANGO_HOST=127.0.0.1`, `DJANGO_PORT=8000`,
`VITE_HOST=127.0.0.1`, `VITE_PORT=5173`, `NO_BROWSER=0`. Docker is not required
and no graphical desktop is assumed.

## Visualization + in-app annotation

The SPA has a built-in **slice viewer** (`frontend/src/features/viewer/`) that
streams PNG slices from the server's slice-IO layer
(`backend/annotation/visualization/slice_io.py`). Volumes are opened as
**memory-maps** and only the current slice (plus prefetched neighbours) is read,
windowed, and PNG-encoded; the client keeps object URLs in a **bounded LRU**
(256, mirroring Cellable's `MAX_SLICE_PIXMAP_CACHE`). This keeps both server RAM
and browser memory flat regardless of volume size.

Providers (defaults now `inapp`):

- `MITO_VISUALIZATION_PROVIDER=inapp` → in-app slice viewer (`/viewer/...`).
- `MITO_PROOFREADING_PROVIDER=inapp` → in-app editor launch (`/editor/tasks/<id>`).

Role gating (enforced in `annotation/services.py`, not just the UI):

- **requester (Institution)** → view only; the launch is downgraded to
  `editable=false` and mutation endpoints return `403`.
- **manager / assigned annotator** → View **and** Annotate entry points.
- requester + annotator read the **same** task labels, so both monitor live
  progress off one source of truth.

### Where viewer latency actually comes from

If the app feels slow, check these in order — each has a specific place to
look (see `progress/history/03-fix-hard-case-share-view.md` item C):

| Symptom | Look at |
| --- | --- |
| Labels → **All** is slow to appear | `cellable_port/labels_3d.py`. The first request per volume per server process scans it (parallel, ~1s for 137x2758x2514, ~3.5s for 5,754 labels); everything after is a cache hit (~20ms). If *every* request is slow, something is invalidating the cache — check that writers call `update_summary_for_slice` (per-slice fold-in) rather than `forget_summary`. |
| The Labels list itself feels heavy to scroll/filter | It windows above 200 rows (`useVirtualRows.ts`). If rows wrap to two lines the spacer maths breaks — keep `.labels-list-virtual li` on one line. |
| Volume size / voxel info slow on a page with many volumes | `core.utils` caches header reads by (path, mtime); a miss is 7-200ms per file. |
| Stepping z is slow / re-fetches slices you just looked at | `AnnotationCanvas`'s `sliceImageUrl` / `labelRunsFor` caches + the z±1 prefetch effect. Image slices are cached for the session (they can't change); label RLE is dropped per-z on Save and wholesale on any whole-volume write. |
| Everything stalls while the 3D panel loads | `Labels3DPanel` aborts superseded mesh requests; a bulk "3D all" is the most expensive request the app makes, and an abandoned one used to keep running and starve slice fetches (the dev server serves few requests at a time). Also check `pinned3D` isn't being rebuilt by unrelated UI — 3D reloads on the pin set only. |
| Another annotator saving makes *your* viewer cold | `slice_io.invalidate_read_caches(path)` / `drop_file(path)` must be called **with the written path**. A bare call clears every file's cached slices for every user. |
| First AI click on a slice takes seconds, repeat clicks too | the embedding cache below (`MITO_AI_TIMING=1` to see `embed source=`). The in-process cache is keyed by the slice's cache-path identity, not by hashing the slice — hashing tens of MB per cursor-follow predict was itself the cost. |

## Fork-aware SAM2 tracking on GPU nodes

SAM2 tracking (`backend/annotation/tracking/`) ports the MTS multi-branch
approach. When a mitochondrion **forks**, each 8-connected branch is seeded as
its own temporary track id, all branches are kept in one `TrackGroup`, and after
propagation the group is **auto-merged into one final instance id**
(`annotation.tracking.services.run_branch_tracking`). Branch ids, the final id,
and group membership are persisted in `volume.metadata['tracking_groups']` for
audit / undo / re-run.

Providers (`MITO_TRACKING_PROVIDER`):

- `local` (default) — dependency-free CPU stand-in for dev/CI (no GPU, no torch).
- `sam2` — the real GPU model
  (`annotation/tracking/adapters/sam2.py` + `sam2_bridge.py`, the latter a
  self-contained port of `MTS/mts_mask_editor/core/sam2_wrapper.py`). It is
  heavy and **must run on a GPU compute node**, never inside the web process.

  The model + weights are **vendored into this repo** at `vendor/sam2/` (a
  full copy of facebookresearch/sam2 + downloaded checkpoints — see
  root `README.md` for provenance), not read from any external `MTS`
  checkout — `MITO_SAM2_ROOT`/`_CHECKPOINT`/`_CONFIG` already default to
  that vendored copy in `config/settings.py`, so no `.env` changes are
  needed to point at it. To actually run it:

  ```bash
  # On the GPU node, into the mito-data-agent conda env from environment.yml:
  export MITO_TRACKING_PROVIDER=sam2   # default in .env.example
  # Weights: vendor/sam2 (Git LFS). Override MITO_SAM2_* only if elsewhere.
  ```

On the cluster, dispatch tracking through the existing processing backend
(`MITO_PROCESSING_BACKEND=slurm`) so the GPU work lands on a compute node via
`sbatch` (bind + tunnel like MTS), while the web tier only creates/polls the job.
For local development the `local` provider runs inline and needs no GPU.

## Cellable-ported interactive AI tools (Point Mask / Box Mask / Boundary / Seeds)

`backend/annotation/cellable_port/` (see that app's `MODULE.md` for what's
ported from where). Unlike SAM2 tracking above, this is **CPU-only and
lightweight** — no torch, no GPU required, safe to install in any dev
environment:

```bash
# Already included when you create the env from environment.yml.
# (onnxruntime, scikit-image, scipy are in environment.yml.)
```

Model weights live under **`vendor/efficient_sam/`** (EfficientSAM **`vits`**
encoder+decoder ONNX only — same tier Cellable defaults to).
`MITO_CELLABLE_MODELS_ROOT` defaults there in `config/settings.py`.

**Two things had to match for masks to actually agree with local Cellable**
(a prior round shipped
`vitt` as a "CPU-friendly" default and fed the encoder a differently-
normalized image, and the user correctly rejected that as not parity):
1. **The weight tier** — `vits`, as above.
2. **The image preprocessing fed to the encoder** —
   `cellable_port/ai/normalize.py`'s `normalize_for_ai` ports Cellable's own
   `normalizeImg` exactly (per-slice, non-zero-pixel 1st/99.5th percentile
   stretch), which is a *different* function from `slice_io.display_range`
   (whole-volume, display-stable, used for the JPEG/PNG streaming
   endpoints) — conflating the two was a real, independent source of mask
   divergence. Observed on a real registered EM volume during verification:
   the exact same point at the exact same weight tier went from "~95% of
   the whole slice" (using `display_range`) to "~0.07% of the slice, a
   tight blob" (using `normalize_for_ai`) — confirming this was the
   dominant bug, not the weight tier alone.

Without the optional dependency installed (or without the model files
present), Point Mask / Box Mask / Boundary degrade to a clear `503`
response (`cellable_port/ai/registry.py`'s `AiUnavailable`) rather than a
crash — Seeds/watershed and every other tool work regardless, since
watershed only needs scipy/scikit-image, not the AI model.

**On a SLURM node, onnxruntime used to flood the terminal** with
`pthread_setaffinity_np failed ... Invalid argument` — harmless (predict
still returned `200`) but noisy: onnxruntime sizes its thread pool from the
node's *physical* core count by default, then tries to pin threads to CPUs
outside a `-c N`-restricted cgroup's affinity mask. Fixed by building
both the encoder and decoder `InferenceSession`s with explicit
`SessionOptions` (`efficient_sam.py`'s `_resolve_thread_count`/
`_session_options`) — reads `SLURM_CPUS_PER_TASK` first, then
`os.sched_getaffinity(0)` (the real, cgroup-aware count), then
`os.cpu_count()`, capped at 8. No `.env`/launcher change needed; if you want
to override the cap for experimentation, set `ORT_NUM_THREADS`/
`OMP_NUM_THREADS` in the environment before starting the server — those are
generic onnxruntime/OpenMP knobs this app doesn't read itself, but they can
still influence the underlying execution provider alongside the explicit
`SessionOptions` above.

**On-disk embedding cache** (`cellable_port/ai/embed_cache.py`, ported idea
from Cellable's `pre_compute_tiff_sam_feature.py`): the encoder's output for
a given (volume, axis, index, model variant) is cached **beside the volume**,
under its dataset folder's
`<project>/<dataset>/embeddings/<variant>/<mask stem>_<axis>_<index>_<mtime>.npy`
(no longer a global `data/embeddings/` silo; `<mask stem>` =
`working_mask_stem`, e.g. `…heart__volume.ome_mask`)
— a fresh Django process (not just a warm in-process LRU) can reuse it, so
revisiting a slice after restarting the dev server still gets a fast,
decoder-only predict. The image's mtime is baked into the filename
specifically so a re-registered/replaced source image can never be served a
stale embedding by accident. Cleared automatically by `clear_dev_data`
("Reset dev data") along with everything else under the data root — nothing
extra to do.

**The filename prefix must match on the write and read side** (both
`working_mask_stem`): if the on-disk cache stops hitting and every Point/Box
click suddenly takes ~3s (the cold vits encode) instead of ~0.2–0.7s, that
mismatch is the first thing to check. Set `MITO_AI_TIMING=1` in the
environment to log per-request timing to the `mito.ai.timing` logger —
`embed source=inproc|disk|encoder`, `decode … ms`, and `predict prep/TOTAL`
— which makes a cache miss obvious immediately. A `disk`/`inproc` embed
source is a hit; a stream of `encoder` sources on repeat clicks of the same
slice means the cache isn't landing where the runtime looks. If old caches
were left behind by a layout change, `python manage.py migrate_volume_artifacts`
self-heals any mis-prefixed `.npy` files into the current scheme. The warm
path (`/warm-embedding/`, fired by the frontend on slice-settle for the
current slice **and both neighbours**) plus a small normalized-slice cache
keep steady-state clicks decode-bound.

## Annotate hotkeys

`frontend/src/features/viewer/AnnotationCanvas.tsx` — moved here (and into
`progress/frontend/features/MODULE.md`) from a permanent footer line under
the canvas, deliberately removed to
give the canvas viewport that row's height back. All hotkeys are still
live — this is the map, not a UI change.

| Key | Action |
| --- | --- |
| `V` | Select (eyedropper — pick the clicked instance) — **default tool** |
| `B` | Brush |
| `E` | Erase (circular) |
| `R` | Box Erase |
| `P` | Point Mask |
| `M` | Box Mask |
| `O` | Boundary |
| `T` | Seeds (3D watershed) |
| `Enter` | Commit the current AI proposal (Point/Boundary: re-predicts committed-points-only first, discarding any live cursor tip — see `26`/`27`/`28`) |
| `Escape` | Clear the AI proposal/prompt points (all of Point/Box/Boundary, including an in-progress Box drag) |
| `Ctrl/Cmd+click` (Point/Boundary) | Add this point, then immediately commit |
| Double-click (Point/Boundary) | Commit the current proposal |
| `Alt+click` an existing prompt point (Point/Boundary) | Remove just that point, re-predict |
| Drag an existing prompt point (Point/Boundary) | Move it, re-predict live while dragging + once more on release |
| `Shift+click`/hover (Point/Boundary) | Negative prompt point |
| `F` | Verify the active label |
| `Shift+R` | Revert the active label to its proposed snapshot (only if `can_revert`) |
| `Delete` | Reject (delete) the active label from the whole volume — behind the same confirm dialog the Filters Options button uses |
| `H` | Toggle Hide Verified |
| `S` | Solo the active label |
| `Shift+S` | Show all (clear solo/hidden) |
| `Ctrl/Cmd+Z` / `Ctrl/Cmd+Shift+Z` | Undo / redo |
| `A`/`D` or `←`/`→` | Previous/next z-slice |
| Wheel over the canvas | Change z-slice (throttled ~40ms) |
| `Ctrl/Cmd+wheel` | Zoom |
| Right-click on the canvas | Minimal context menu — mode switches, plus Verify/Solo if over a label |

All hotkeys are disabled while the "Swap 3D ↔ Canvas" view is active, except the label-lifecycle ones
(`F`/`Shift+R`/`Delete`/`H`/`S`/`Shift+S`), which mirror the always-enabled
Filters Options buttons and aren't "annotate edit" in the painting sense —
Undo/Redo *are* blocked even via keyboard in that state, since they mutate
the raster the same way painting does.
