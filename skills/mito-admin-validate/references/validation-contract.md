# Validation contract

## Storage and metadata

Compare the actual new volume/task identities with the authorized manifest. Check source SHA256/receipts, required keys/dtypes, matching shape, physical units, label scientific status, local task bounds and intended assignments. Verify reserved/other project rows are absent from the write set. Read source headers and representative voxels as the service account.

Snapshot preexisting volume/task/submission records and compare after the operation. A DB comparison cannot prove that filesystem bytes stayed unchanged; also inspect the operation's source/working/submission write set and its hash evidence. Treat concurrent human changes as real activity, not defects to overwrite.

## Viewer and editing

Open the actual deployed viewer with an authorized session. Resolve its routes and parameter conventions from the current frontend. Use a nonempty ROI plane and wait for completed loading. Verify imagery, prediction colors, ROI alignment, axis switching, assignment/read-only status and relevant request errors. Record IDs, source coordinates, screenshots and expectations. A screenshot alone is not an exact voxel-alignment assertion.

If a new loader/profile needs edit validation, exercise the production API implementation on an isolated DB clone/generated-data root. Check all axes, region overlay voxels against the source, label-ID preservation, a bounded reversible ROI edit/save/reopen, restored working-file SHA256, source hashes, rollback and generated scratch cleanup. Honor HTTPS redirects/host configuration when using APIClient. A transaction rollback does not undo filesystem writes. Do not use a human annotation as a test target.

Keep temporary auth private. Revoke only a token created for this test, preserve an existing token, and never archive credentials. Existing UI and permission boundaries remain in force.

## Pyramids and validation claims

Use the official build_pyramid(volume, layer='image'/'region') and its readiness contract. Record actual validated flag, samples/chunks checked, bytes and runtime. Distinguish sampled derivative validation from full-file source hash verification. Do not describe sampled checks as exhaustive voxel equivalence.

The P10 case passed metadata, three-axis image reads, exact region-plane alignment, prediction-ID preservation, reversible edit/save/reopen/restoration, unchanged sources and rolled-back engineering rows. Its actual pilot viewer loaded correctly; image/region builders each validated24 sampled chunks. The bulk import's remaining54 volumes used native HDF5 without pyramids. This is a dated case, not validation of the current deployment or a requirement to rerun all tests for every documentation change.
