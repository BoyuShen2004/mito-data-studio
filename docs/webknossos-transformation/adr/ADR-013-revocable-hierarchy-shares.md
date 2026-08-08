# ADR-013: Revocable hierarchy shares

## Status

Accepted and implemented (2026-08-03).

## Decision

Anonymous project, dataset, and volume sharing uses an unguessable token stored
in `projects.PublicShare`. The row carries exactly one scope plus its owning
project and optional dataset/volume. It also records creator and revoker audit
facts. Tokens are random secrets, not signed serialized state: authorization is
resolved from the current database row on every browse/view request.

A project token may browse all of its datasets and volumes; a dataset token is
restricted to that dataset; a volume token is restricted to one volume. All
three feed the existing read-only `AnnotationCanvas` adapter. No public route
maps to paint, autosave, tool apply, submission, or other mutation endpoints.

Revocation sets `revoked_at` instead of deleting the row. Subsequent use of the
same token returns HTTP 410 with `The manager closed this share.` This preserves
an explicit distinction between a mistyped link and a deliberately closed one.

The manager dashboard aggregates live rows into a project → dataset → volume
tree. A live project link makes its project LED fully shared; otherwise the
project state is derived from dataset and ungrouped-volume children. Dataset
LEDs use the same rule over volumes, while volume LEDs are binary.

**Parent Stop is direct-scope only.** Stopping a project link does not revoke
dataset or volume links, and stopping a dataset link does not revoke volume
links. The remaining descendants continue to drive the aggregate parent LED.
This avoids a deceptively small button cascading into unrelated links created
by other collaborators. Managers may stop any volume link, including one
created by an annotator; non-managers may stop only links they created.

## Consequences

- Managers can list and revoke live links from the SPA.
- Nested browse remains server-authorized; hiding a volume in React is never an
  access control boundary.
- Revocation takes effect without key rotation or process reload.
- Parent controls cannot silently revoke descendant links.
- Existing hard-case and legacy task links remain compatible and independent.
