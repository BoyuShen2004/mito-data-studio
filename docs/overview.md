# Product overview

## Purpose

Mito Data Studio is a browser-based system for organizing, annotating,
proofreading, reviewing, and sharing three-dimensional microscopy instance
segmentations. Its primary application is mitochondrial annotation in electron
microscopy volumes, while the data model and tools operate on generic 3-D
intensity volumes and integer label masks.

The system combines a role-aware work-management application with an
interactive slice editor. It keeps registered image data immutable, maintains a
separate working label draft, and installs submitted snapshots as official labels
through explicit review decisions. Working-team withdrawal can also promote a
saved draft without approval; official does not itself mean reviewed.

## Roles

| Role | Primary responsibilities |
| --- | --- |
| Requester | Creates projects, registers datasets, specifies work, monitors progress, and views deliverables |
| Manager | Approves projects, controls access, assigns volumes, manages teams, reviews submissions, and publishes or revokes read-only shares |
| Annotator | Edits assigned volumes, records difficult cases, saves drafts, and submits snapshots for review |

These are three workspaces. An annotator may also hold **assistant manager**
access, granted/revoked by an existing manager identity or superuser. The account
keeps its annotation work and switches between Annotator and Manager using the
username dropdown. Manager mode has application-wide management scope, while
Annotator mode retains ordinary project/assignment scope. Assistant managers
cannot grant this capability to others. Legacy database roles remain readable.
See [roles and navigation](user-guide/01-roles-and-navigation.md).

## Functional scope

### Data and project management

- Project and dataset creation with workflow types for annotation,
  proofreading, or segmentation.
- Server-side registration of image, region-mask, and initial-label paths.
- Automatic filename pairing for common TIFF/HDF5/NIfTI and nnU-Net layouts,
  with manual review before registration.
- Shape, dtype, and available physical voxel-size inspection.
- Team membership, explicit project access, task assignment, transfer,
  priority, difficulty, deadline, and instructions.

### Visualization

- Axial, coronal, and sagittal slice viewing over the internal `(z, y, x)`
  array convention.
- Layer navigation, zoom, pan, intensity controls, label opacity, visibility,
  solo/pinning, and region-mask overlays.
- Optional validated Zarr v3 image and region-mask pyramids for chunked
  streaming, with the original source retained as a fallback.
- Three-dimensional label surfaces generated with Gaussian smoothing and
  marching cubes, rendered in the browser with Three.js.

### Annotation

- Brush, erase, rectangular erase, merge, connected-component split, flood
  fill, 3-D watershed, and between-slice interpolation.
- Empty-only/all-voxel policies for fill, interpolation, Track and outside-ROI
  presentation; Brush and committed masks can replace unprotected IDs.
- Point-, box-, and boundary-prompted SAM 2 proposals.
- SAM 2.1 propagation across inclusive z ranges with automatic branch
  inference, contact handling, merge/reseed continuation, and batch preview.
- Pending browser edits, Undo/Redo, explicit Save, best-effort autosave, Verify's
  save flush, and revision-aware writes.
- Region-only editing that protects content outside the immutable ROI.

### Research extensions

The core workflow stays focused on registration, assignment, annotation and
review. Research-specific analysis uses a shared, rightmost **Extensions** tab.
Measurements demonstrates this composition: a typed registry entry and lazy
adapter reuse the project context while computation remains in explicit backend
APIs and processing jobs. It is the only built-in tool currently registered.

Contributors can add or disable tools without adding a new core tab. Disabling
an entry changes discovery, not stored results or job lifecycle. Extensions are
trusted code shipped with the application and require a rebuild/deployment;
there is no runtime third-party installer. See the
[contribution contract](engineering/extensions.md). Lazy loading and modular
composition do not establish biological validity or measured scalability.

### Measurements

- Measurements extension under the rightmost project **Extensions** tab with per-volume official-label or saved-draft sources.
- Physical spacing read from registered metadata or supported source headers; manual entry for missing values.
- Per-instance voxel count, physical volume, skeleton cable length and CSV export.
- Explicit manager-triggered background jobs; labels remain unchanged.
- See the [measurement guide](user-guide/09-measurements.md) for scope and limits.

### Review, provenance, and collaboration

- Separate in-application and uploaded-file submission channels.
- Immutable submission snapshots and manager decisions: approve and close,
  approve and keep open, request revision, or reject.
- Approval promotes the selected snapshot; registered source images and region
  masks are never rewritten.
- Hard-case records with discussion, status, focused viewer entry, and optional
  independently revocable public links.
- Read-only project, dataset, volume and hard-case shares with revocation.
  Legacy signed task shares remain read-only but lack per-link revocation/expiry.
- Annotation-time accounting that excludes inactive/read-only sessions and
  avoids double-counting overlapping intervals.
- Append-only audit vocabulary for access, assignment, submission, review, and
  reset events.

## Explicit non-goals and boundaries

- The application is not a clinical diagnostic system.
- AI results are proposals, not ground truth. Accepted masks enter the draft
  buffer; unresolved Track previews also enter it and are not excluded by autosave.
  See the [Track caveat](user-guide/06-assisted-and-track.md#failure-and-safety-rules).
- A single Track request is not automatically sharded across all visible GPUs.
- Zarr pyramids are application-specific Zarr v3 derivatives; the repository
  does not currently claim OME-NGFF, Neuroglancer, or Fileglancer compatibility.
- Editable labels use a TIFF working copy rather than a mutable Zarr pyramid.
- Registration records existing server paths; it is not a general-purpose
  microscopy format converter.

