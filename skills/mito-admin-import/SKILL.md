---
name: mito-admin-import
description: Stage and register verified volume manifests in Mito Data Studio using its production services, with calibration, capacity, duplicate prevention, and unassigned-task handling.
---
# Mito Data Studio administrative imports
Use the application's own services for DB registration and readiness. Inspect the repository's requirements and running deployment first; use the preflight skill when source/access/space/calibration remain unresolved.

Read [import-contract.md](references/import-contract.md). Resolve the requested project, source profile and service wrapper from live state. Historical P10 helpers in the lab handoff are not universal importers: never reuse their project IDs, host paths, HDF5 keys or spacing for another dataset. This repository skill package contains no host-bound mutation script.

1. Honor the requested subset. Generate disjoint operator/team manifests and reject duplicates already registered elsewhere. Do not consume a teammate's reserved exercise automatically.
2. Capture the existing project/dataset/volume/task/submission state and a fresh DB backup; preserve referenced payloads. Hash source files on persistent storage. Stage to a distinct local directory through partial files, full SHA256 checks, atomic rename and reader ACLs. Keep a reserve and future label/derivative budget; do not allocate every future label in advance.
3. Before a new profile's bulk operation, exercise one representative nonempty-ROI volume. Validate keys/dtypes/shapes, spacing units, axes and region alignment using real app reads. If edit behavior must be tested, use an isolated generated-data root plus a rolled-back DB clone or an equivalent isolated deployment, restore edited bytes, and verify original source hashes. Never use a human annotation as a smoke-test target.
4. Register through get_or_create_dataset, register_volume(..., enqueue_pyramid=False), and create_whole_volume_task after confirming current signatures. Whole-volume task bounds are local zero-based half-open extents. Prediction imports remain predictions; create unassigned tasks unless the user requested assignment. Do not change global feature flags.
5. Use one bounded transaction for metadata creation, source-path identity for idempotency, and conflict checks against existing project names. Preserve source receipts and calibration in metadata. A transaction rollback does not remove generated filesystem payloads or undo sequence increments; account for both explicitly.
6. Follow with validation before claiming completion. Publish new IDs, actual counts, checksums/receipts and remaining capacity. Do not mark unbuilt pyramids ready or report unreviewed predictions as proofread.

For deterministic execution, save a wrapper with explicit project/dataset/root, source profile, spacing units, manifest and receipts. Give it a read-only preview and a separate apply mode; validate it on a bounded isolated case before using a new profile. The existing application services own registration semantics. Read the referenced contract rather than inventing SQL or marking readiness yourself.
