# 8. Hard cases, sharing, time, profile, and safety

[User guide](../user-guide.md) · Previous: [Submit and review](07-submit-and-review.md) · Next: [Measurements](09-measurements.md)

## Hard cases

Hard cases are project discussions about difficult labels; they are separate
from submission-round review comments.

1. In Annotate, make the relevant label active and select **Record hard case**.
2. Add an optional primary note and one category, then confirm recording.
   Blank remains **Uncategorised**; selecting the current category again clears it.
3. Open the item from the project's **Cases** tab, or from Home — **Open cases**
   for a manager, **Cases in my projects** for an annotator.
4. Project members can read and reply. The creator and managers can revise the
   note/category, resolve/reopen, or revoke the public link. Resolving retains
   internal history and leaves its public link live until revoked. Annotate is
   available only to a creator/manager with live edit access to the underlying task.

Categories are Uncertain, Under-segmented, Over-segmented, Not a mitochondrion,
Cut off by the volume boundary, and Something else, plus blank Uncategorised.

Opening a hard case returns to a plane containing the label and solos it by
default. Use it for durable cross-role discussion; use an instance review
comment when feedback belongs to one submitted round.

## Public sharing

Managers can create revocable project and dataset shares. Any authenticated user
with volume-view access can create a volume share, including requesters and
annotators. Only a manager or that volume share's creator can revoke it. A
project link opens a dataset browser, a dataset link opens its volume table, and
a volume link opens the viewer. A volume link copied from the viewer can capture
the current axis, position, and label focus.

Hard cases have independent public links. Every public page is clearly
read-only and requires no account. A public recipient cannot annotate, submit,
join a project, or gain broader access by editing the URL.

Use **Copy link** after **Share**. Use **Stop sharing** to revoke the live link;
revoking a hard-case link does not remove the internal case. Treat links as
bearer access: send them only through an appropriate channel and revoke them
when no longer needed.

Stopping a project/dataset share revokes only its direct token(s), not separately
created child volume/dataset tokens. Check the **Shares** tree for remaining live
links. Reusing a live volume share reuses its token and original creator. Viewer
context can preserve axis/coordinates/focus, but the server still checks the
selected volume against the token scope. A public link shows live visible labels,
not a frozen submission snapshot. Revocation stops later reads; it cannot revoke
copies a recipient already downloaded. Authenticated viewer links require the
recipient's own project access; public token routes require no login.

Legacy `/share/task/<token>` links still open a read-only task viewer. Their
signed tokens have no per-link revocation record and no age expiry check;
removing the task invalidates its lookup, but ordinary Stop sharing on a volume
will not invalidate such a token. Use database-backed volume shares when you
need per-link revocation. The legacy task-share API lets authenticated task
viewers mint these tokens; do not treat them as revocable volume-share records.

## Annotation time

Time accrues only for the assigned annotator while an eligible editable viewer
is open and active. Read-only viewing, manager inspection, hidden/inactive
browser time, and another user's session do not count. Heartbeats stop abandoned
tabs, and overlapping eligible sessions do not double-count the same wall-clock
interval.

Time is cumulative across saves, submissions, revisions, and reopen cycles. A
transfer preserves earlier time under the person who performed it. Managers can
inspect task totals and project/dataset/volume breakdowns; annotators see their
own totals.

- `-` means the volume predates tracking and its historical total is unknown.
- `0m` means tracking is enabled but no eligible time has accrued.

Timing supports operations and attribution; it should not be treated as a
standalone measure of biological quality or worker performance.

## Profile and shortcuts

Open the username in the navigation bar to edit personal information and
contact details. Annotators and managers can customize the tool shortcuts that
are pressed with `Ctrl` (`⌘` on macOS) plus a letter; the plain-letter keys in
[Manual annotation tools](05-annotation-tools.md#keyboard-shortcuts) are not
affected. Shortcuts are saved to the account and follow it between browsers;
each shortcut must be unique. **Reset to defaults** changes the form, and **Save
profile** persists it.

## Safety checklist

- Save before submitting, navigating away, or closing the browser.
- Resolve every live assisted-mask or Track preview.
- Verify active label, overwrite policy, axis/range, and region-only state before
  a broad edit.
- Use Undo for recent pending edits; treat whole-task Reset as permanent draft
  deletion.
- Remember that public shares are read-only but still expose the shared data to
  anyone holding the link.
- Errors can appear as a popup or an inline status/error, notably in Measurements
  and forms. Record the message and report it with project, dataset, volume, task,
  axis, layer, and affected data layer.

[User guide](../user-guide.md) · Previous: [Submit and review](07-submit-and-review.md) · Next: [Measurements](09-measurements.md)
