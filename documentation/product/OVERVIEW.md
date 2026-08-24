# Product overview

## Purpose

Mito Data Studio is a browser-based system for organizing, annotating,
proofreading, reviewing, and sharing three-dimensional microscopy instance
segmentations. Its primary application is mitochondrial annotation in electron
microscopy volumes, while the data model and tools operate on generic 3-D
intensity volumes and integer label masks.

The system combines a role-aware work-management application with an
interactive slice editor. It keeps registered image data immutable, maintains a
separate working label draft, and requires explicit review decisions before a
submitted result becomes the official label.

## Roles

| Role | Primary responsibilities |
| --- | --- |
| Requester | Creates projects, registers datasets, specifies work, monitors progress, and views deliverables |
| Manager | Approves projects, controls access, assigns volumes, manages teams, reviews submissions, and publishes or revokes read-only shares |
| Annotator | Edits assigned volumes, records difficult cases, saves drafts, and submits snapshots for review |

Legacy database roles remain readable for compatibility, but the current user
experience is organized around these three roles.

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
- Conservative empty-voxel-only overwrite by default, with explicit
  overwrite-all mode where supported.
- Point-, box-, and boundary-prompted EfficientSAM proposals.
- SAM 2.1 propagation across inclusive z ranges with automatic branch
  inference, contact handling, merge/reseed continuation, and batch preview.
- Pending browser edits, Undo/Redo, explicit Save, and revision-aware writes.
- Region-only editing that protects content outside the immutable ROI.

### Review, provenance, and collaboration

- Separate in-application and uploaded-file submission channels.
- Immutable submission snapshots and manager decisions: approve and close,
  approve and keep open, request revision, or reject.
- Approval promotes the selected snapshot; registered source images and region
  masks are never rewritten.
- Hard-case records with discussion, status, focused viewer entry, and optional
  independently revocable public links.
- Read-only project, dataset, volume, task, and hard-case shares.
- Annotation-time accounting that excludes inactive/read-only sessions and
  avoids double-counting overlapping intervals.
- Append-only audit vocabulary for access, assignment, submission, review, and
  reset events.

## Explicit non-goals and boundaries

- The application is not a clinical diagnostic system.
- AI results are proposals, not ground truth, and are never silently saved.
- A single Track request is not automatically sharded across all visible GPUs.
- Zarr pyramids are application-specific Zarr v3 derivatives; the repository
  does not currently claim OME-NGFF, Neuroglancer, or Fileglancer compatibility.
- Editable labels use a TIFF working copy rather than a mutable Zarr pyramid.
- Registration records existing server paths; it is not a general-purpose
  microscopy format converter.

