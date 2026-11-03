# Discover the actual deployment

Do not assume that the repository checkout is the running application or that a familiar host alias is available. The operator supplies the intended instance and project. Inspect these facts read-only before mutation:

- Active deployment identity, repository revision, virtual environment, service account and service confinement. Check source paths with the effective reader account, not only as root.
- Django project/dataset IDs and names, source paths and uploaded FileFields, task bounds/assignment/review/lock state, working-label basenames and submission paths. Use the model/service code in this checkout to resolve field meanings.
- Database engine/version and backup mechanism. Read credentials in-process through settings; do not echo settings or put secrets into argv. A matching-version DB dump is metadata, not a backup of referenced HDF5/TIFF payloads.
- Source storage access and persistent destination access, local filesystem capacity, ACLs, open readers, job queues, service dependencies and rollback scripts. An SSH URI is not a local loader path. Record concurrent space commitments.
- Image/prediction/region profiles: HDF5 keys, dtype, shape, axis order, coordinate/bounds conventions, physical spacing and units. Consult the generating pipeline and its provenance. Do not guess calibration from a filename or a presentation override.

The relevant implementation is projects/services.py, volumes/services.py, annotation/services.py, annotation/label_paths.py and volumes/pyramid/. Repository paths are relative to backend/ unless stated otherwise. Inspect current signatures before calling them.

## Worked-case lessons, not deployment defaults

The 3 November 2026 P10 run split111 remaining volumes into55 operator and56 teammate rows. All165 operator source files were SHA256-verified. Existing80 volumes/tasks and85 submissions were preserved. Only the pilot received validated image/region pyramids;54 new volumes used native HDF5.

That source pipeline established native ZYX30,8,8 nm, stored as0.030,0.008,0.008 micrometres. It is a P10 calibration, not a global microscopy default. Its source copies used65.70GiB; eventual operator working labels could require136.28GiB before snapshots and derivatives. The reserved56 required66.54GiB of sources and138GiB of working labels. A five-volume exercise used4.78GiB of sources and10GiB of working labels. The run's20GiB reserve was a chosen operational floor, not storage preallocation or a universal requirement.

Submitted work was not treated as disposable. A directory named Finished was version-archived with provenance without promoting it to canonical scientific labels. The illustrated guide has the dated deployment-specific examples; current live state remains authoritative.
