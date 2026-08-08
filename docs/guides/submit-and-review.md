# Submit and review

Saving and submitting are separate actions. **Save** writes pending layers to
the working draft. The editor's **Submit for review** creates an immutable
snapshot in the **Online (in-app)** channel. Unsaved browser edits are not part
of that snapshot.

## Annotator

1. Finish the current edit and press Save.
2. Confirm the editor no longer says Unsaved.
3. Choose **Submit for review** in the task top bar.
4. If the manager requests revision or rejects the result, reopen Annotate,
   make changes, Save, and choose **Submit again**.

The task Details page also has an **Offline annotation upload** card for a
completed label file. Online and offline submissions are independent: a new
submission replaces only the pending round in its own channel, while the other
channel remains available for review. Older rounds and their decisions remain
in Review history.

## Manager

Open **Reviews** from the manager dashboard. A task can show one pending
**Online (in-app)** submission and one pending **Offline (file upload)**
submission at the same time; each row is tagged with its channel. You can:

- **Approve & close:** update the official label and close annotation.
- **Approve & keep open:** update the official label but allow more annotation
  and another review round.
- **Request revision:** return the task with comments.
- **Reject:** return the task without updating the official label.

Revision requested and Reject both hand work back to the annotator. Approval
is the action that resolves the competition: the chosen channel's immutable
label file is copied to an app-owned official path, the other pending channel
is marked Voided, and the editable working copy is discarded and freshly
seeded from the approved official label. Image and region-mask sources are not
changed. Reject or Request revision affects only the reviewed channel; another
pending channel remains reviewable.

Review history persists the channel on every decision, so an accepted result
continues to read **Approved — Online (in-app)** or **Approved — Offline (file
upload)** after reload. A manager controls closing and reopening annotation in
**Project → Assign → Details**. Approve closes by default; **Approve & keep
open** permits another round after the same official/working reset.
