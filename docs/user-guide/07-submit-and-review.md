# 7. Save, submit, review, and feedback

[User guide](../user-guide.md) · Previous: [Region and assisted tools](06-assisted-and-track.md) · Next: [Collaboration and safety](08-collaboration-and-safety.md)

## Save is not Submit

1. **Save** writes pending browser edits to the task's mutable working draft.
2. **Submit for review** creates an immutable snapshot of the saved draft.
3. A manager decision may promote that snapshot to the official checkpoint or
   return the task for more work.

Always save and confirm there are no unsaved changes before submission. A
submission does not implicitly include edits that still exist only in browser
memory.

## Two submission channels

- **In-app submission** snapshots the working draft and can be inspected in the
  browser with **View**.
- **Offline annotation upload** accepts a completed label file through the task
  submission page and records QC information.

A task may have one pending in-app and one pending offline submission. They are
reviewed independently until one is approved. Do not assume uploading a file
replaces the current in-app draft.

## Manager review

The review page shows task, annotator, source channel, round, notes, QC status,
previous decisions, and any commented instances. For an in-app snapshot, use
**View** to inspect the immutable submission. **Annotate** opens the task's
current working context; distinguish it from the submitted snapshot before
making changes.

Available decisions are:

| Decision | Result |
| --- | --- |
| Approve & close | Install the snapshot as the official checkpoint and lock further painting/submission |
| Approve & keep open | Install the checkpoint but allow another annotation and review round |
| Request revision | Return the reviewed channel for more work; do not change the official mask |
| Reject | Return the reviewed channel; do not change the official mask |

Approval voids the competing pending channel and starts a fresh working copy
from the approved checkpoint. A later open round starts a fresh per-label
verification lifecycle. Source images and region masks are never modified.

## Comment on a specific instance

While reviewing a submission in the viewer, right-click a labeled mitochondrion
and choose **Comment on label #…**. Enter a focused correction or question. The
comment records the submission round, label ID, and current axis/3-D location.

The manager sees these under **Commented instances** and may delete an erroneous
comment before deciding. The annotator sees feedback under **My Tasks → Manager
feedback**. **View** or **Annotate** reopens the task at the recorded view and
solos/focuses the label, which is more precise than describing a location only
in free text.

Instance comments supplement the overall decision comment. They do not edit the
label and do not by themselves change task status. If an instance was merged or
renumbered after the reviewed snapshot, compare against the cited submission
round before acting.

## Revision loop

1. Annotator opens the feedback item at its saved location.
2. Annotator verifies the cited submission/round and corrects the working draft.
3. Save until no unsaved changes remain.
4. Submit a new snapshot through the same intended channel.
5. Manager reviews the new round and records a decision.

[User guide](../user-guide.md) · Previous: [Region and assisted tools](06-assisted-and-track.md) · Next: [Collaboration and safety](08-collaboration-and-safety.md)
