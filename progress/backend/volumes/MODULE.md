# `backend/volumes/` — Volume registration + HPC data discovery

Owns the `Volume` model (one image/label pair) and everything about getting
data *into* the app: scanning HPC directories, pairing image files with
mask files by filename convention, registering them as volumes, and
splitting a volume into z-range annotation tasks. The biggest and most
intricate file in the whole backend by logic-density is `services.py`
(867 lines) — most of it is filename-pairing heuristics, not business logic.

## Model (`models.py`)

**`Volume`** — one image (+ optional label) pair.
- `project` (denormalised from `dataset.project` — tasks/assignment/progress
  all query by project directly, so the two are kept in step by
  `register_volume`/`set_volume_dataset`, never edited independently).
- `dataset` — nullable, only for rows predating the `Dataset` model.
- `name` — canonical volume identity (optional rename at register time).
- Image: `image_path` (registered by reference, a path under
  `MITO_DATA_ROOT`) **or** `image_file` (uploaded `FileField`) — exactly one
  is normally set. `image_location` property returns whichever is set,
  file taking precedence.
- Region mask: optional read-only ROI/region reference
  (`region_mask_path`/`region_mask_file` → `region_mask_location`). It is not
  an annotator's working label and annotation never overwrites it.
- Label: optional initial editable-label source, using the same pattern
  (`label_path`/`label_file` → `label_location`), plus
  `label_type` (`none`/`prediction`/`proofread`/`partial` —
  `core.choices.LabelType`, drives what task type splitting produces).
- `shape_z/y/x`, `voxel_size_z/y/x` — best-effort, filled by
  `_try_autodetect_shape` from file headers (`core.utils`) if not supplied.
- `has_label` property: `label_type != NONE and (label_path or label_file)`.

**Data safety: be careful with anything that *writes* to
`image_location`/`label_location`.** A real
externally-referenced label file was overwritten during this project's
development because a write path didn't distinguish "the app's own copy"
from "a path registered by reference to someone else's data." The fix lives
in `annotation.services` (`_save_label_volume`/`_writable_label`), not
here, but the *reason* it's dangerous is this dual `path`/`file` pattern —
`label_path` can be **any absolute path on disk**, not necessarily
something this app owns.

`label_path` is the volume's **official, approved** label — in-app edits (paint or
SAM2 tracking) never touch it directly; they write to a separate *working*
copy (`annotation.label_paths.working_label_rel_path(volume)`,
`<project name>/<dataset name>/<image stem>_mask.tif` under `MITO_DATA_ROOT`
— named from the volume's registered image basename, with a sibling
`metadata/` sidecar and `embeddings/<variant>/` SAM cache in the same dataset
folder; no global `data/embeddings/` silo, no `volume_<id>_labels.tif`
naming — organized to mirror the project → dataset → volume hierarchy the
frontend shows, so the on-disk layout under `data/` is legible to a backend
developer). `label_path` only changes when a
manager **approves** an in-app submission
(`annotation.services.approve_submission` →
`_promote_working_label_to_official`) — see `backend/annotation/MODULE.md`'s
"Label persistence" section for the full split.

## Data registration pipeline (`services.py`)

`SUPPORTED_DATA_EXTENSIONS` is the gate: a file whose name doesn't end with
`.tif`, `.tiff`, `.h5`, `.hdf5` or `.nii.gz` is never even listed by the
directory browser. HDF5 files are registered by reference like any other
source and read in place (`annotation/visualization/hdf5_io.py`) — there is
no transcoding step and no second copy on disk. Note that pairing keys off
name *tokens*: `..._im.h5` / `..._mask_pc2.h5` differ by the trailing `pc2`,
so files like that are paired explicitly in the UI rather than automatically.

The filename-pairing logic (`pair_by_case`, `detect_volume_pairs`,
`case_key`, `pairing_key`, `channel_index`, `_is_mask_name`) exists so a
requester can point at a directory of files with **inconsistent naming**
(e.g. `sample01_em.tif` + `sample01_mask.tif`, or `case3.tif` +
`case3_label.tif`) and have them auto-paired by inferred "case" identity,
without a rigid naming convention. Rough shape:
1. `_stem`/`_name_tokens` normalize a filename to comparable tokens.
2. `_is_mask_name` flags likely-mask files by keyword (`mask`, `label`,
   `seg`, ...).
3. `pairing_key` strips image, mask, and region role tokens from both sides so
   an image/mask pair reduces to the same case-insensitive key. Broad matches
   must be unique; ambiguous names are left for manual pairing.
4. `case_key`/`channel_index` handle multi-channel/multi-case naming.
5. `pair_by_case`/`detect_volume_pairs` produce the final `(image, mask)`
   pairs plus a list of unmatched files (surfaced to the user to resolve
   manually).

**Registration flow**, roughly:
1. `scan_data_sources(image_directory, mask_directory, region_mask_directory)` (or
   `scan_hpc_directory` for a single combined directory) — lists files,
   detects pairs, reads any `read_dataset_manifest` (a JSON manifest a
   directory can ship with pre-declared pairs/metadata, checked first —
   `_manifest_pairs_for` — before falling back to filename heuristics).
   Also `suggest_sibling_directories` — if you point at an image dir, it offers
   likely label folders (`_looks_like_mask_dir`/`_looks_like_image_dir`). The
   caller must explicitly choose whether a suggestion is an editable-label
   source or a read-only region-mask source; a suggestion is never both.
2. `register_dataset(created_by, dataset, volume, image_directory,
   mask_directory, pairs, files, label_type, metadata, project,
   annotation_type, reviewed)` — the actual write: creates/reuses the
   `Dataset`, creates one `Volume` per pair via `register_volume`.
3. `register_volume(project, name, image_path/file, label_path/file,
   label_type, file_format, voxel_size, metadata)` — creates the `Volume`
   row, autodetecting shape via `_try_autodetect_shape` if not given.
4. `update_volume_metadata(volume, **fields)` — the general-purpose editor
   used by `VolumeDetailView.update` (metadata merges, everything else
   replaces).

**Turning a volume into its task:**
- `infer_task_type(label_type, override=None)` →
  `core.choices.LABEL_TYPE_TO_TASK_TYPE` (e.g. a volume with `prediction`
  labels becomes a `prediction_proofreading` task by default).
- Task creation itself lives in `annotation.services`
  (`create_whole_volume_task` / `ensure_volume_tasks`). One volume is one
  assignable work unit, so there is deliberately no service here that turns
  a volume into several tasks — the old `split_volume_by_frames` /
  `create_tasks_from_volume` frame-splitting pair was removed.

## API (`api.py`)

- `HpcScanView` — `POST /api/hpc/scan/`, requesters+managers
  (`CanRegisterData`). Wraps `scan_data_sources`.
- `RegisterDataView` — `POST /api/register-data/`, the shared
  requester/manager registration endpoint. Requesters may only register
  into **their own** projects (checked against `project.created_by_id`).
  Manager-registered data is `reviewed=True` on creation; requester data
  stays pending.
- `ProjectVolumesView` — list/create volumes under
  `/api/projects/<project_id>/volumes/`; same ownership check pattern.
- `VolumeDetailView` — retrieve/update/delete a single volume.
  - `get_object` re-checks ownership (managers, or the owning requester)
    on every access, not just list.
  - `update` blocks moving a volume to a dataset the caller doesn't own.
  - `destroy` — `?force=1` bypasses the `DeleteBlocked` guard (409 with
    `{"detail", "counts"}` otherwise), same pattern as `projects`.
- `VolumeDependentsView` — pre-delete warning data (`GET`).

There is no split endpoint. The `manager_reviewed` review gate for task
creation is enforced by the Assign flow in `annotation` instead.

## Gotchas

- `image_location`/`label_location` are **properties**, not DB fields — you
  can't `.filter(image_location=...)` on them; filter on `image_path`/
  `image_file` directly if you need a queryset-level check.
- Requester ownership checks are duplicated across several views
  (`ProjectVolumesView.get_project`, `VolumeDetailView.get_object`,
  `RegisterDataView.post`) rather than centralized — if you add a new
  volume-touching endpoint, copy the `is_manager(user) or
  project.created_by_id == user.id` pattern rather than assuming a shared
  helper exists.
