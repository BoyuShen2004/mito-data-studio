# Microscopy data and storage contract

## Logical layers

Each volume can reference three separate arrays with identical spatial shape:

| Layer | Mutability | Meaning |
| --- | --- | --- |
| Image | Immutable | Intensity source used for viewing and model inference |
| Region mask | Immutable | ROI/focus mask; nonzero means inside |
| Instance label | Source immutable; working copy mutable | Zero background and positive integer object IDs |

The software does not infer channel names, biological meaning, or missing
physical calibration. Voxel size is stored as `(z, y, x)` when the source
provides valid metadata. Unknown calibration remains unknown at the product
boundary; some display/pyramid calculations use documented isotropic fallback
values internally and must not be reported as measured physical resolution.

## Axis conventions

The application-wide array convention is `(z, y, x)`.

- Axial slices index z and have plane shape `(y, x)`.
- Coronal slices index y and have plane shape `(z, x)`.
- Sagittal slices index x and have plane shape `(z, y)`.
- Track propagation is axial only.
- TIFF, HDF5, and NIfTI 3-D sources use on-disk `(z, y, x)` here. NIfTI is
  **not** remapped or reoriented from medical `(x, y, z)`; pixdim follows on-disk
  order. Re-register volumes imported under the removed transposing reader.
- NIfTI allows 2-D (exposed as `(1,y,x)`) and trailing singleton dimensions;
  non-singleton trailing channel/time dimensions are rejected. HDF5 supports
  2-D/3-D effective arrays and leading singleton dimensions.
- TIFF slice I/O prepends z to 2-D arrays and flattens extra leading dimensions
  into z. It does not semantically validate/reorder OME channel/time axes. Supply
  one spatial volume per file; readable OME/ImageJ spacing is not proof that a
  multi-channel/time TIFF has been interpreted as intended.
- HDF5 datasets may have leading singleton dimensions, which are pinned to
  zero; ambiguous multi-volume files are rejected rather than guessed.

## Registration input formats

The current registration surface accepts:

- TIFF: `.tif`, `.tiff`, including OME-TIFF metadata where readable;
- HDF5: `.h5`, `.hdf5` (the lower-level reader also recognizes `.he5`);
- NIfTI: `.nii`, `.nii.gz`.

HDF5 selection checks conventional dataset keys and otherwise requires a
single unambiguous numeric 3-D dataset (or 2-D if no 3-D candidate exists).
TIFF and NPY are supported by lower-level slice I/O where used internally, but NPY is not a current registration
extension. `FileFormat` retains Zarr and N5 vocabulary for model compatibility,
but this does not mean the registration UI currently accepts arbitrary Zarr or
N5 sources.

## Working labels

Official labels can originate in TIFF, HDF5, or NIfTI. Editing is performed in
an owned, writable, memory-mappable TIFF working copy. HDF5/NIfTI sources seed
that copy in bounded blocks rather than becoming writable in place. Save is
revision checked and label-state metadata is stored separately from voxels.

This separation prevents annotation from modifying registered data and gives
submission/review a stable snapshot boundary.

## Label lifecycle and source selection

Submit copies the saved working file into an immutable submission snapshot;
subsequent saves do not update its bytes. Approval copies the winning snapshot
into `approved/`, updates both the official label reference and reset-seed
metadata, then resets/re-seeds the working TIFF and label lifecycle state. Reset
therefore uses the latest registered reset seed, which may now be an approved
result, rather than always the originally imported label.

Team withdrawal is a separate tested promotion path: it may repoint the official
label directly to the saved working TIFF without a review or immutable approval
copy. Official and working references can then alias. Scientific consumers
must record the source/decision history, not infer approval from the word official.

Stale revision-checked saves reject before replacing newer geometry. Snapshot
bytes are immutable through ordinary application operations; review status and
supersession metadata are mutable, and deleting dependent work can remove snapshots.
Measurements read official or saved working files and do not mutate annotation
state; pending browser edits and selectable submission snapshots are excluded.

## Pyramid derivatives

Optional image and region-mask derivatives are separate Zarr v3 groups under
the dataset's owned `pyramids/` directory. Arrays are named by xy magnification
and record full `(z, y, x)` factors, shapes, dtype, source, voxel-size values,
reducer, build time, and deterministic checksum seed.

- Intensity data use mean reduction.
- Region masks use categorical mode reduction.
- Downsampling is anisotropy aware and operates in bounded z slabs.
- Chunking is slice oriented, normally `(1, 512, 512)` clipped to the level.
- Builds write to a `.building` sibling and are promoted only after sampled
  chunks reproduce source-derived SHA-256 digests.
- Editable labels do not currently have a mutable pyramid.

These are application-specific Zarr v3 attributes. They are not an
OME-NGFF `multiscales` declaration. Neuroglancer/Fileglancer compatibility has
not been validated and is therefore not claimed.

## Encoding and transport

Label planes and prompt masks use run-length encoding in JSON APIs. Large image
views may use chunk tokens and validated pyramids; the original full-plane
slice path remains a fallback. AI embedding files are cached under owned
per-volume paths keyed by model variant, source identity/mtime, axis, layer,
and ROI.

## Owned artifacts and deletion

Everything the application writes lives under `MITO_DATA_ROOT`, in one folder
per project and, beneath it, one folder per dataset. Folder names are derived
by `annotation/label_paths.py` (`project_folder_rel_path`,
`dataset_folder_rel_path`). A dataset folder holds volume working masks and
their lock files, label-state metadata, `pyramids/`, `embeddings/<model
variant>/`, and approved labels; submission uploads are kept per task under
`submissions/task_<pk>/`.

Deleting a project, dataset, or volume plans its file cleanup before the
database rows are removed and performs it only after the transaction commits
(`projects/services.py`). The cleanup removes the targeted per-volume generated
artifacts of every deleted volume, plus the deleted dataset's or project's folder. A project or
dataset that survives keeps its folder; artifact subfolders emptied by the
delete are pruned. It protects current registered image, label and region-mask
paths, including those inside the data root, as well as surviving volumes'
references, external paths and symlinks. Historical imported labels are not
tracked separately: approval replaces the label reference, so an earlier import
inside an owned dataset/project folder may be removed by subsequent tree
cleanup. Archive originals independently; this sequence lacks a regression
test (see [audit B08](../research/documentation-audit.md#ambiguities-and-suspected-implementation-bugs-no-runtime-changes)).
An app-generated approved label is an exception: it can be
removed with its deleted volume even when currently referenced as its label
source. Failed removal is logged and does not fail the database delete.

Generic `processing_jobs/<id>/` results/logs are outside these per-volume cleanup
lists. ProcessingJob rows survive with null domain links and their artifacts
are not automatically purged by volume/dataset/project deletion. Measurement
results are consequently retained historical artifacts; deleted-volume inputs
cannot be current. Reset has a separate comprehensive cleanup contract.

## Data-integrity rules for studies

- Archive original sources separately from working and derived artifacts.
- Record source checksums, shapes, dtypes, dataset keys, voxel sizes, and units.
- Record the software commit and migration state used for annotation.
- Do not interpret fallback `(1,1,1)` values as physical calibration.
- Validate label ID semantics after any external conversion.
- Report whether a study used source-plane or pyramid/chunk viewing.

