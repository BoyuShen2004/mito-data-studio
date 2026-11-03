---
name: mito-admin-preflight
description: Inspect Mito Data Studio datasets, source volumes, calibration, access, dependencies, and storage before an administrative import or archive operation.
---
# Mito Data Studio preflight
Use this for administrative data preparation, not annotation proofreading.

Read [deployment.md](references/deployment.md) to discover the running deployment. Resolve current IDs, paths, calibration, permissions and capacity before a write. Read repository AGENTS.md and product invariants.

1. Establish the requested outcome and existing authorization. Separate inspection, copying, registration, and local retirement. Existing authorization persists; do not insert a new approval checkpoint for an already authorized phase. Installing or loading a skill grants no system privileges or authority to delete data.
2. Inspect the running application's project/dataset identities and current source paths, annotation tasks, assignment/review/lock states, working labels, submissions, and backup references. Read actual service code rather than inferring API behavior from names. A task being submitted is not proof that its data can be retired.
3. Inventory image/prediction/region triplets by manifest. Check names, HDF5 keys, dtype, ZYX shape, origin/bounds, and coverage. Resolve voxel spacing and units from the source pipeline/provenance; record conflicting metadata and explain the authority used. Never transplant the P10 calibration to another dataset.
4. Check SSH source access, local loader paths, service-account readability, active file readers, and backup/restore dependencies. Andromeda storage is persistent; an ssh path is not a local HDF5 loader path. A skill cannot create missing credentials or grant manager access.
5. Budget incremental source copies, uncompressed working labels, snapshots, and derivatives separately. Use current f_bavail under the writing account and any concurrent commitments. The P10 run used a 20 GiB reserve; choose a suitable explicit reserve for the actual job. Do not sum overlapping Docker reclaimable estimates.
6. Save a disjoint manifest, checksums/provenance, capacity calculation, protected live paths, and a concrete next action. If the full job does not fit, identify a bounded subset and the remaining capacity gap rather than claiming readiness.

Use `scripts/manifest_capacity.py --help` for a read-only manifest budget. It requires no Django or network. A successful budget is only a storage calculation, not validation of every dependency.

Continue with the bundled archive/import/validation skill as appropriate. If a required credential, calibration, or storage resource is absent, report that specific prerequisite; continue independent authorized checks.
