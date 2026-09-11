# Attribution register

This is the concise companion to [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
Add an entry before committing copied, modified, ported, or integrated upstream
code. Preserve upstream copyright and license notices.

## Vendored components

| Component | Upstream revision | License | Repository path | Relationship |
| --- | --- | --- | --- | --- |
| SAM 2 | facebookresearch/sam2 `2b90b9f5ceec907a1c18123530e92e794ad901a4` | Apache-2.0 | `vendor/sam2/` | Pinned source, config, and checkpoint |

EfficientSAM was vendored here until 2026-09-09 and has been removed along with
its weights; see `THIRD_PARTY_NOTICES.md` for the full record.

## Cellable provenance

Several modules under `backend/annotation/cellable_port/` (prompt handling,
image normalization, watershed, component split, merge, label state, and 3-D
label summaries) were ported from the Cellable desktop annotator. Each keeps a
one-line note naming the function it came from. The upstream license and
redistribution terms are not yet recorded here; that audit is an open item in
`documentation/release/RELEASE_CHECKLIST.md`.

## WEBKNOSSOS provenance

No WEBKNOSSOS source has been copied, modified, or ported into this repository.
The application independently implements behaviors studied from WEBKNOSSOS
documentation and a read-only reference checkout at commit `a24aecc6f`.

If that changes, record the upstream repository, path, commit, license,
relationship, destination, and preserved notices here before the code is
committed. Copying AGPL-covered source into a network application may create
repository-wide source-distribution obligations; obtain an appropriate license
review first.

