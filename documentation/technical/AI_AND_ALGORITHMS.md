# AI models and annotation algorithms

## Design principle

Model output is assistive. SAM 2 creates proposals or pending plans that
remain subject to human inspection, Confirm/Reject where applicable, and the
editor's explicit Save. The software does not retrain the model and does not
represent its output as validated biological ground truth.

## Interactive segmentation

Point, box and boundary prompts run on the vendored SAM 2 checkpoint — the same
weights Track propagates with, loaded once per worker and shared between the
two rather than held twice. Provenance and hashes are in
`THIRD_PARTY_NOTICES.md`.

The application:

1. reads a 2-D plane in the selected axis;
2. computes a bounded prompt-centered ROI;
3. normalizes the image and converts grayscale to RGB;
4. obtains or computes the image-encoder features;
5. decodes positive/negative point or box prompts;
6. removes small predicted components;
7. optionally derives a boundary using binary dilation XOR erosion;
8. returns the mask as a run-length encoded pending proposal.

Encoder features are cached twice: an in-process LRU per worker, and a shared
float16 on-disk cache so a worker that has not seen a plane loads it instead of
re-encoding. The model runs on CUDA when a GPU is available and on the CPU
(slowly) otherwise; if SAM 2 or its weights cannot load, the tools report
themselves unavailable rather than substituting a weaker model.

For a point prompt, SAM 2's candidate masks are first restricted to components
that contain a positive click, then chosen by predicted IoU under a
plane-fraction limit (`MITO_AI_MASK_MAX_PLANE_FRACTION`), a relaxed limit
(`MITO_AI_MASK_FALLBACK_MAX_PLANE_FRACTION`), and finally the smallest anchored
candidate under a hard cap. The browser's hover tip is display chrome and is
never sent as a prompt.

A prompt ROI with sides between 128 and 1024 px is upscaled to 1024 px before
encoding, and the mask is scaled back to the ROI.

The image predictor is created under the same lock as encoding and decoding, so
concurrent first requests after a worker starts cannot race to build it, and
checkpoint loads are serialized across workers. A runtime error during
prediction (for example a transient CUDA out-of-memory while workers load the
model) returns HTTP 503 with a retry message instead of a 500; the browser
retries once after 750 ms before reporting it.

Relevant settings are `MITO_AI_ROI_*` (prompt ROI size and padding),
`MITO_AI_MASK_MAX_PLANE_FRACTION` and `MITO_AI_MASK_FALLBACK_MAX_PLANE_FRACTION`
(candidate limits), `MITO_SAM2_IMAGE_CACHE_SLOTS` (in-process feature cache,
default 8), and `MITO_AI_PRELOAD` (load SAM 2 in the background as each worker
starts, default on). A study must record their effective values.

Primary reference: Ravi et al., “SAM 2: Segment Anything in Images and
Videos,” arXiv:2408.00714 (2024), <https://arxiv.org/abs/2408.00714>.

## SAM 2.1 volumetric Track

The repository vendors pinned SAM 2 source/configuration and the official
`sam2.1_hiera_large.pt` checkpoint. The recorded upstream commit is
`2b90b9f5ceec907a1c18123530e92e794ad901a4`; the configured model is SAM 2.1
Hiera Large (`configs/sam2.1/sam2.1_hiera_l.yaml`). The browser treats z-slices
as an ordered frame sequence and supplies mask prompts on selected layers.

For one queued class, the backend:

1. validates an explicit inclusive z range;
2. splits disconnected seed geometry into deterministic branches, discarding
   components smaller than the configured minimum;
3. assigns temporary provider object IDs;
4. clusters distant prompts into bounded xy windows;
5. initializes a mutable SAM2 video-predictor session and propagates prompts in
   both directions;
6. resolves branch associations and contacts in canonical z order;
7. when branches merge, combines the contact mask and re-seeds the survivor for
   downstream continuation;
8. merges surviving temporary IDs into the requested final class ID;
9. returns changed planes as one pending preview.

Default branch/contact parameters include a four-pixel minimum component, a
scale-aware centroid distance factor of 2.0, an ambiguity IoU margin of 0.05,
eight pixels for strong single-layer contact, two pixels sustained across two
layers for weak contact, and a three-layer rolling area window. These are
software defaults, not biologically optimized constants, and can be overridden
through settings. A study must record effective values.

Batch classes run in request order; earlier classes win protected collisions.
The model is a process-local singleton protected by a lock. Hardware detection
selects conservative workers/devices/crop limits but does not shard a single
batch across all GPUs.

Primary reference: Ravi et al., “SAM 2: Segment Anything in Images and Videos,”
arXiv:2408.00714 (2024), <https://arxiv.org/abs/2408.00714>. Official model
repository: <https://github.com/facebookresearch/sam2>.

## Deterministic annotation algorithms

- **Connected-component split:** separates disconnected regions and allocates
  new positive instance IDs.
- **Watershed:** runs scikit-image watershed over a bounded 3-D crop using a
  distance-transform surface and user seeds.
- **Interpolation:** computes intermediate masks between reviewed endpoint
  layers with physical in-plane spacing when known.
- **Flood fill:** fills a connected target region inside a bounded block.
- **Merge/delete:** plans label-ID replacements or clearing while protecting
  verified labels and respecting overwrite policy.
- **3-D surfaces:** applies light Gaussian smoothing and scikit-image marching
  cubes at level 0.5, then renders meshes with Three.js.
- **Pyramids:** use mean reduction for intensities and most-common nonzero mode
  for categorical masks, with edge padding and anisotropy-aware factors.

## Licensing and provenance

SAM 2 is carried with its Apache-2.0 license text (`vendor/sam2/LICENSE`).
Several modules under `backend/annotation/cellable_port/` — prompt handling,
image normalization, watershed, component split, merge, label state, and 3-D
label summaries — were ported from the Cellable desktop annotator. Each keeps a
one-line provenance note, and their redistribution terms are still to be
audited (see the release checklist). WEBKNOSSOS informed architecture and
behavior, but no WEBKNOSSOS source is recorded as copied into this repository.
See `THIRD_PARTY_NOTICES.md` and `docs/attribution.md` before publication or
distribution.

## Scientific limitations

- SAM 2 is not fine-tuned in this repository for a specific EM dataset.
- Model accuracy depends on modality, contrast, voxel anisotropy, seed quality,
  crop size, and selected z range.
- Software correctness tests do not establish biological segmentation quality.
- Throughput measurements from one GPU or volume must not be generalized
  without reporting hardware, volume shape/dtype, prompt count, and settings.

