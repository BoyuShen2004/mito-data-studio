# Archive classification and restore

## Dependency checks
Inspect actual Volume image/label/region paths, uploaded FileFields, working label basenames, tasks, submissions/reviews/locks, service readers, job queues, symlink targets, and backup restore scripts. Submitted is distinct from approved/locked. Approval does not by itself prove a source or derivative has no remaining readers. Preserve provenance linking finished outputs to their source grid and original labels.

For a file being hashed: record lstat, open fd and fstat, hash in bounded chunks, compare fd before/after, then compare current path inode/device/size/mtime. Keep source and archive receipt identities explicit. Any unexpected change stops that leaf's retirement. Use unique version directories; never overwrite a canonical scientific label based on a filename guess.

Archive-copy verification can be remote hashing over SSH without transferring bytes back. Keep credentials out of manifests. A private manifest can contain full restore paths; avoid publishing backup payloads in a shared guide.

## Restore and dependency evidence

Keep an exact private retirement receipt with original and archive paths, SHA256/size, allocated bytes, owner/group/mode, mtime, scientific classification and dependency assessment. Do not publish private backup payloads or credentials in a skill repository.

Restore the exact archived payload to its original location, verify its SHA256, and restore ownership/mode as appropriate before invoking a rollback procedure. Inspect an existing target first; never overwrite newer human work automatically. Verify that an alias/symlink resolves to an identical retained cache rather than to another retirement candidate.

In the P10 worked case, only an exact reviewed cold-copy set was retired after fresh archive/local verification; live annotation/source paths and separately archived Finished originals were retained. Disabling an inactive duplicate model unit and preserving its reader path avoided breaking dependencies. Those particular files/hosts are case-specific, not a reusable cleanup allowlist.
