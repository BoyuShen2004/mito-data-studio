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
lazy adapters under a single `(z, y, x)` on-disk axis contract. Physical
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
edited assigned volumes. Browser edits entered a pending buffer saved explicitly,
by Verify's flush, or by best-effort 30-second/hidden-tab autosave. Backend tool
plans did not write label files. The current Track implementation stages previews
before Confirm and autosave does not exclude them; this limitation must be
reported rather than claiming confirmation guarantees a persistence barrier. Submission created an immutable
snapshot. Manager approval promoted the selected snapshot to the official
label, while rejection or revision returned that submission channel for additional
work; a pending sibling kept the task submitted. Working-team withdrawal could
also repoint official labels to saved working TIFFs without review approval.

The editor provided manual painting and erasing, label merge, connected-
component split, flood fill, 3-D watershed, and between-slice interpolation.
Fill, interpolation and Track used explicit empty-only/all-voxel policies.
Brush and committed prompted masks could replace unprotected label values;
verified labels were protected. Split used 26-connectivity and cleared components
below 100 voxels. Browser interpolation used pixel geometry, while separate
backend interpolation APIs could use physical spacing.

## Interface organisation

The interface was organised around a single unit of work rather than around
roles. A task — one volume and one active assignee, with a manager deciding each round — was presented
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

This history was **derived at render time** from durable records (assignment
timestamps, submission rounds, and immutable review decisions) rather than
materialised in a task-conversation event table. Separate append-only audit
events are persisted for selected actions; derived conversation history does
not imply absence of audit rows or other denormalized workflow fields.

## Interactive segmentation

Point-, box-, and boundary-prompted masks were generated with the SAM 2.1 Hiera
Large image predictor, built on the same loaded weights used for axial
propagation. Inference used a prompt-centered ROI; planes with sides between
128 and 1024 px were upscaled to 1024 px before encoding and the mask scaled
back. Encoder features were cached in memory per worker and in a shared float16
on-disk cache. For point prompts, candidate masks were restricted to components
containing a positive click and selected by predicted IoU under a plane-fraction
limit, with a relaxed limit and then the smallest anchored candidate under a
hard cap as fallbacks. The application provider required CUDA, with CPU fallback disabled;
when CUDA or the model could not be loaded, the tools reported themselves unavailable
rather than substituting another model. A runtime error during prediction
returned a retryable response, which the client retried once. Predictions were
returned as pending
run-length encoded masks for human inspection and explicit saving.

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
Rejected through the pending edit buffer. The queue/prompt state was durable
separately from label voxels; confirmation retired propagated queue entries. The model's mutable inference state was
serialized within each worker.

## Quality and provenance controls

The SPA used API tokens, with session authentication also supported; session and
reset writes enforced CSRF according to their respective API gates. Public
database-backed shares were scoped/read-only/revocable, while legacy signed
task tokens lacked per-link revocation and age expiry.

The implementation used role- and project-scoped authorization, revision-aware
label writes, explicit source/working/snapshot separation, deterministic
pyramid validation, and automated backend/frontend tests. Deleting a project,
dataset, or volume removed the targeted working/submission/pyramid/cache/approved
artifacts after commit, protecting current registered source paths and surviving
references. Earlier imported labels whose references were replaced by approval
were not separately tracked for cleanup protection. Original inputs required
independent archives. Generic processing-job history and outputs were retained. Annotation activity
was recorded only for eligible active editing sessions, excluding read-only or
inactive browser periods and merging overlapping intervals to avoid double
counting wall-clock time.

## Research-tool composition and delegated management

The project interface separated the core registration, assignment, annotation
and review workflow from a rightmost Extensions catalog. Measurements was the
first implemented tool. A typed, repository-owned registry described enabled
entries and lazily mounted a selected workspace with project/volume context;
backend authorization and scientific job routing remained explicit. Disabling
an entry did not remove existing results or cancel jobs. This provided a common
frontend contribution path for research-specific tools, not runtime plugin
installation or a measured scalability/usability result.

Assistant-manager access was stored as an additional annotator capability.
Existing base manager identities or superusers granted/revoked it with an audit
record. Dual-role users selected Annotator or Manager in their current browser
tab; token-authenticated requests validated the selected role against database
grants. Annotator mode retained ordinary scope; Manager mode exposed the global
management workflow but could not delegate assistant access. Switching required
confirmation and returned to Home without saving pending work. Profile listed
both identities. See [role design](../engineering/roles-and-workspaces.md) and
[extension contract](../engineering/extensions.md) for implementation limits.

## Measurements

Manager-triggered `measure_mito` ProcessingJobs ran in the local dispatcher via
a Python runner, separately from interactive SAM2. Official and saved working
label files were explicit sources; browser pending edits and selectable
submission snapshots were excluded. Per-ID voxel count and calibrated volume
included all components. Kimimaro TEASAR skeletonization omitted components
below 100 voxels; cable length summed edge distances in physical units. Same-ID
components shared one padded bounding box; web crops exceeding 8,000,000 voxels
failed without publishing partial results. Whole-volume measurement did not
clip by ROI or change annotation state. Metadata fingerprints and spacing were
checked before/after web computation. See the [implementation reference](../engineering/measurements.md)
for parameters, units, export/provenance limits and deployment prerequisites.
Synthetic tests establish software behavior, not biological validity.

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

