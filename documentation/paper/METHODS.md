# Manuscript-ready methods description

This file is a factual starting point for a future Scientific Reports Methods
section. Adapt tense and study-specific details only after the corresponding
experiment, dataset, ethics, and validation records exist.

## Software system

Mito Data Studio was implemented as a browser-based three-dimensional
microscopy annotation and review system. The backend used Django and Django REST
Framework, while the client used React and TypeScript. PostgreSQL stored users,
projects, datasets, task state, permissions, submissions, review decisions,
time intervals, processing jobs, and audit metadata. Voxel arrays remained in
filesystem-backed microscopy files and derived stores rather than database
blobs. The system separated immutable registered images and region masks from
mutable working label copies and immutable submission snapshots.

## Data representation

Volumes were represented internally in `(z, y, x)` order. Intensity images,
optional nonzero ROI masks, and integer instance-label arrays were required to
share spatial shape. TIFF, HDF5, and NIfTI inputs were read through format-aware
lazy adapters. NIfTI `(x, y, z)` arrays were explicitly transposed. Physical
voxel sizes were retained when valid source metadata were available and were
otherwise treated as unknown for scientific reporting.

Optional read-only Zarr v3 pyramids were constructed additively from image and
ROI sources. Intensity levels used mean reduction and categorical ROI levels
used mode reduction. The anisotropy-aware ladder downsampled axes according to
their current physical extent. Builds operated in bounded z slabs, wrote to a
temporary sibling store, and were promoted only after deterministic sampled
chunks matched source-derived SHA-256 digests.

## Annotation workflow

Requesters registered datasets and specified work; managers approved projects,
controlled access, assigned whole volumes, and reviewed submissions; annotators
edited assigned volumes. Browser edits remained pending until explicit Save.
Model and deterministic whole-volume tools returned preview plans, permitting
inspection and rejection before persistence. Submission created an immutable
snapshot. Manager approval promoted the selected snapshot to the official
label, while rejection or revision returned it for additional work.

The editor provided manual painting and erasing, label merge, connected-
component split, flood fill, 3-D watershed, and between-slice interpolation.
The conservative overwrite policy modified only background voxels unless the
user explicitly selected overwrite-all. Verified labels were protected from
Track overwrite.

## Interface organisation

The interface was organised around a single unit of work rather than around
roles. A task — one volume, one assignee, one reviewing manager — was presented
as a numbered, stateful item with a discussion history, and a flagged label
("hard case") used the same presentation. Tasks, hard cases, and projects were
therefore rendered by one list component with one row layout (state, title,
stable numeric identifier, most recent event and its actor, category, assignee),
and one personal home presented each role's queues as saved filters over that
list rather than as separate screens. Filter state was held in the URL query
string so that a narrowed view was a shareable address.

A task's page presented its history as a single chronological sequence
interleaving assignment, each submission round with its channel, and each
review decision with the reviewer's comment, with the corresponding action —
the review form for a manager, painting and submission for the assignee —
placed at the end of that sequence. Reviewing therefore occurred on the page
that carried the evidence, and the resulting state change was displayed in
place rather than by navigation.

This history was **derived at request time** from durable records (assignment
timestamps, submission rounds, and immutable review decisions) rather than
materialised in a per-event table. The system consequently stores no row per
user action, which was a deliberate constraint: an earlier notification inbox
that grew one row per action per recipient was removed, and derived quantities
such as progress and elapsed time are likewise recomputed rather than cached.

## Interactive segmentation

Point-, box-, and boundary-prompted masks were generated with the EfficientSAM-S
ONNX encoder/decoder. Inference used a prompt-centered ROI and cached image
embeddings. CUDA execution was requested when configured, with an observable
CPU fallback. Predictions were returned as pending run-length encoded masks for
human inspection and explicit saving.

## Axial propagation

Multi-slice Track used the official SAM 2.1 Hiera Large checkpoint and treated
z-slices as an ordered frame sequence. Annotators queued instance classes,
painted mask seeds, and specified inclusive axial ranges. Disconnected seed
components were inferred as branches and propagated bidirectionally within
bounded xy crops. Branch contact was resolved deterministically in z order;
merged masks re-seeded the surviving prompt for downstream continuation.
Temporary branch identifiers were collapsed into the requested final instance
identifier.

Multi-class batches loaded one bounded combined z slab and processed classes in
request order, preserving the rule that earlier classes win protected label
collisions. The combined result was reviewed on the canvas and Confirmed or
Rejected as one compound pending edit. The model's mutable inference state was
serialized within each worker.

## Quality and provenance controls

The implementation used role- and project-scoped authorization, revision-aware
label writes, explicit source/working/snapshot separation, deterministic
pyramid validation, and automated backend/frontend tests. Annotation activity
was recorded only for eligible active editing sessions, excluding read-only or
inactive browser periods and merging overlapping intervals to avoid double
counting wall-clock time.

## Required study-specific additions

Before manuscript submission, add:

- specimen, microscopy acquisition, preprocessing, and dataset inclusion rules;
- number of volumes, voxel sizes/units, shapes, dtype, and label definitions;
- annotator training, expertise, blinding, assignment, and adjudication;
- exact software release/tag/commit and effective environment variables;
- hardware allocation and warm/cold inference conditions;
- accuracy endpoints, reference-standard construction, statistical analysis,
  confidence intervals, and handling of missing/failed cases;
- throughput and usability protocols, including baselines and sample sizes;
- ethics, consent, data availability, code availability, and competing interests.

Do not convert unit tests or anecdotal annotator feedback into scientific
accuracy or productivity claims.

