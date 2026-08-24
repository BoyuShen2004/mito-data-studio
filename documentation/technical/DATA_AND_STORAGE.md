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
- NIfTI sources are stored by nibabel as `(x, y, z)` and explicitly transposed
  by the adapter; non-singleton channel/time dimensions are rejected.
- HDF5 datasets may have leading singleton dimensions, which are pinned to
  zero; ambiguous multi-volume files are rejected rather than guessed.

## Registration input formats

The current registration surface accepts:

- TIFF: `.tif`, `.tiff`, including OME-TIFF metadata where readable;
- HDF5: `.h5`, `.hdf5` (the lower-level reader also recognizes `.he5`);
- NIfTI: `.nii`, `.nii.gz`.

HDF5 selection checks conventional dataset keys and otherwise requires a
single unambiguous numeric 3-D dataset. TIFF and NPY are supported by lower
level slice I/O where used internally, but NPY is not a current registration
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

## Data-integrity rules for studies

- Archive original sources separately from working and derived artifacts.
- Record source checksums, shapes, dtypes, dataset keys, voxel sizes, and units.
- Record the software commit and migration state used for annotation.
- Do not interpret fallback `(1,1,1)` values as physical calibration.
- Validate label ID semantics after any external conversion.
- Report whether a study used source-plane or pyramid/chunk viewing.

