# Import contract

Inspect projects/services.py, volumes/services.py, annotation/services.py, annotation/label_paths.py, the active loader and volumes/pyramid/ under backend/. Use the running deployment's Python environment and effective service account. Administrative skill discovery does not grant application or host permissions.

## Manifest and source profile

Resolve explicit project/dataset identity, local source root, image/prediction/region keys and dtypes, ZYX shapes, physical spacing and units, provenance and requested subset. Reject empty/duplicate names and validation errors. Record original source paths, hashes, bytes and stable modification times. If no ROI or prediction layer is requested, honor that profile instead of fabricating one.

For the read-only budget helper, the supported CSV columns are name, shape_zyx (for example256x2048x2048), triplet_bytes and working_label_uint16_bytes; optional validation_errors must be empty. The latter is checked as product(shape)*2. This helper budgets uint16 labels; adapt the calculation for another working dtype or layout. It does not read volume payloads, contact SSH or validate DB state.

## Copy and backup

Hash remote sources in bounded reads; check unchanged size/mtime across the hash. Transfer to unique partial paths under the intended local root, compare every full SHA256/size, then atomically rename and set reader ACLs. Do not open source files for writing. Log each verified volume/role receipt. Resume the same run after interruption; account for any already staged bytes rather than reserving a second full copy.

Before metadata mutation, capture existing project rows and create a fresh matching-version DB backup with credentials kept out of stdout and argv. Verify a persistent backup copy and its archive catalog. Keep all referenced source/label/submission payloads or independently recoverable archives; a database dump does not include those bytes.

## Registration and replay

Use get_or_create_dataset, register_volume(..., enqueue_pyramid=False), and create_whole_volume_task after confirming signatures. Store calibration in the units expected by this deployment. Prediction imports stay prediction labels; tasks remain unassigned unless assignment is requested. Whole-volume task bounds are zero-based half-open local extents.

Stage and verify before one bounded metadata transaction. Inside the wrapper, validate local source readability and required headers as the actual service account; reject manifest/path/identity conflicts before changing rows. Preserve source receipts and scientific status in metadata. Do not create rows with bulk SQL or bypass model/service invariants.

The existing register_volume service treats dataset plus image source path as an idempotent identity and rejects conflicting metadata. Check project names too, so the same source is not introduced in a different dataset by another importer. A returned row is not necessarily newly created: compare before/after IDs and report replay honestly. Do not reassign or overwrite a task that someone has begun working on. Coordinate overlapping team manifests before applying; source-path identity is not a batch-ownership lock.

Generated files and sequence increments may survive a DB rollback. Keep smoke tests in a separate generated-data root, retain their evidence, and clean only that generated scratch. Do not restore a full DB snapshot over concurrent human work as an automatic failure response.

## Capacity and readiness

Budget incremental source bytes plus working labels, snapshots, derivatives, temporary files, reserve and other commitments. The budget helper does not reserve storage. Native HDF5 fallback is acceptable where supported. Build only an explicitly budgeted pilot pyramid via the official builder; never force readiness flags or flip global features. Use the validation skill before declaring completion.
