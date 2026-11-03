---
name: mito-admin-archive
description: Archive Mito Data Studio research assets and historical dependencies with recoverable copies and verification; retire local duplicates only when the requested scope authorizes it.
---
# Verified administrative archiving
Create a reversible archive and dependency/restore evidence. This skill is not a blanket cleanup permission.

Read [archive-contract.md](references/archive-contract.md). Classify candidates against the live DB, service configuration, open readers, jobs, caches, backup pointers, and rollback scripts. Separate raw source, working label, submitted snapshot, approved output, derivative, demo copy, model cache, and historical backup. Preserve distinctions in the plan.

1. Construct an exact file manifest with source/destination, bytes, SHA256, mode/owner/mtime, scientific status, and dependencies. Deduplicate names by content, not filename. A directory named Finished is not evidence of approval.
2. Copy to a new versioned location on persistent storage. Preserve original filenames and provenance. Use a private destination for backups/user-specific payloads and the project's existing group access for shared scientific handoff files; do not broaden sharing casually.
3. Verify every copy by full source/destination SHA256 and size, and check that source size/mtime stayed stable while hashing. Keep receipts and a restore mapping. Transfer exit0 or matching sizes alone is not verification.
4. If the user requested copy-only, stop at a verified archive. If local retirement of the concrete candidates is already authorized, proceed without asking again: recheck dependencies, freshly verify destination content, freshly hash each local regular file, and verify unchanged inode/device/size/mtime before unlinking that exact leaf. Refuse an unexpected symlink or a changed/missing source. Log each completed unlink durably. Do not recursively delete a parent containing unlisted data.
5. Preserve dependency behavior: resolve a retired reader path using a checked restore pointer or an identical active cache when appropriate. Do not create a symlink whose target is itself scheduled for retirement. Keep active models and required recent rollback copies.
6. Report gross allocated bytes reclaimed separately from net free space after concurrent imports. Document restore paths and verify that the retained live data are still accessible.

Do not infer permission to destroy submitted/approved annotations, reset production, or change ownership/access from a general data-management request. If the user authorized a specific reviewed retirement plan, that authorization is sufficient for that plan.
