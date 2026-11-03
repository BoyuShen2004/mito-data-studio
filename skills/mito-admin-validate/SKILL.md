---
name: mito-admin-validate
description: Verify Mito Data Studio volume imports, viewer layers, task state, and pyramid readiness while preserving existing annotations and source payloads.
---
# Validate administrative data changes
Read [validation-contract.md](references/validation-contract.md) for the checked P10 evidence and an implementation outline. Use a scope-appropriate check: inspection-only changes do not require repeating an edit test.

- Compare new rows to the authorized manifest: exactly one intended dataset identity, shapes/keys/dtypes, service-account source access, source hashes/receipts, calibration units, label_type and scientific status. Verify whole-volume bounds and intended assignment state. Ensure reserved rows were not imported.
- Compare existing volume/task/submission records to the pre-operation snapshot. Report concurrent human changes honestly; never overwrite them to force a clean comparison. Check the operation's actual filesystem write set rather than treating DB equality alone as proof that bytes stayed unchanged.
- Open an actual new volume with a legitimate authorized session. Verify raw imagery, prediction labels, region overlay, axis switching, loading completion and relevant request errors. Screenshots should show a representative nonempty ROI; a shell return code is not a viewer check. Do not expose tokens in screenshots or logs.
- Use the official build_pyramid(volume, layer='image'/'region') only within the requested capacity budget. Record its validation report and readiness flags. Native HDF5 fallback is a valid import result where supported. Do not fabricate readiness for an unbuilt derivative, globally enqueue every volume, or change feature flags to make validation pass.
- If save/reopen correctness needs testing for a new loader/profile, use an isolated clone and separate generated-data root. Check axis images, exact region-plane voxels, label-ID preservation, a reversible ROI edit/save/reopen, final restored working-file hash, unchanged source hashes and cleanup/rollback. Existing human work is outside that smoke test.
- Keep temporary browser credentials private and revoke only a token created specifically for the test; preserve an existing token. Remove generated scratch only after its evidence is saved. Do not archive credentials into a handoff.
- Deliver verified counts, actual capacity, IDs/links, receipts and remaining prerequisites. Save private recovery artifacts separately from a shared tutorial. Posting Slack/email is not implied by validating an import; require the user's explicit instruction to send.
