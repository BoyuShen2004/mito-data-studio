# User guide

This manual describes the current development version of Mito Data Studio.
Screens and actions are permission-aware, so two users may see different
controls on the same project.

## Roles and navigation

- **Requester** creates projects, registers server-readable microscopy data,
  monitors delivery, and views results.
- **Manager** approves projects, manages access and working teams, assigns one
  whole volume to one annotator, reviews submissions, and controls shares.
- **Annotator** edits assigned volumes, saves working drafts, records hard
  cases, and submits snapshots for review.

Use **Profile** to edit personal information. Annotators and managers can also
choose their annotation-tool shortcut letters there. Shortcut letters must be
unique. Access to a project does not by itself assign annotation work.

## Create a project and register data

1. Create a project from **Projects** or **Register data**.
2. Enter a dataset name and a server-side image directory.
3. Optionally enter separate region-mask and editable-label directories.
4. Select **Scan**, verify every image/region/label pairing, choose the label
   type, and register the dataset.

Registration records paths; it does not upload or rewrite sources. Supported
sources include TIFF, HDF5, and NIfTI. Each row may have three distinct layers:

- **Image**: immutable intensity data.
- **Region mask**: optional immutable focus/ROI data; nonzero means inside.
- **Editable label**: optional starting instance segmentation.

Shapes must match. A region mask never becomes an editable label. Image and
region pyramids build in the background; the original source remains usable
while a build is pending or failed. Managers can build, rebuild, or retry a
pyramid from the volume page.

## Access, teams, and assignment

After approving a project, a manager adds annotators through project **Access**
or the project's working team in **People**, then opens **Assign** and selects
one assignee for each volume. One volume is one whole-volume task; there are no
frame splits or multiple active assignees.

Open a row's **Details** to set priority, difficulty, deadline, and instructions,
or to close/reopen annotation. **Reset annotations** discards that task's
working edits, Track prompts, and verification state, then restores the
registered starting mask. It keeps the task and assignment, never modifies the
registered source, cannot be undone, and requires reopening an approved/locked
task first.

## View and annotate

The viewer supports axis changes, typed layer navigation, zoom/pan, intensity
and label opacity, label visibility/solo controls, and 3-D label inspection.
Managers and the assigned annotator can enter **Annotate**; requesters and
public-link recipients remain read-only.

The editable label is a working draft. Browser edits are pending until **Save**
is pressed. Undo and Redo operate on pending edits. A successful Save is
revision-specific, so a newer concurrent edit remains pending instead of being
silently cleared. The editor's **Reset labels** has the same destructive scope
as the manager reset and asks for confirmation.

Choose an active label before painting. The overwrite policy controls whether a
tool may replace only empty voxels or all voxels. Available tools include Brush,
Erase, Merge, Split, Flood fill, Watershed, Interpolate, assisted Point/Box/
Boundary masks, and SAM2 Track when its optional runtime is configured. Preview
or model output is still pending work; it is never saved automatically.

### Region-only mode

When a volume has a region mask, **Only inside region mask** focuses the viewer
on whole label instances that touch the ROI and protects outside content from
ordinary edits. **Jump to region** moves to a nonempty ROI plane. Split and
Watershed may use an explicitly reviewed bounded 3-D result. Save stays explicit
and warns when protected outside-region edits would be omitted.

### SAM-assisted masks and Track

Point uses ordinary clicks for positive prompts and Alt-click for negative
prompts. Box uses a dragged rectangle. Press Enter to commit a proposal or
Escape to cancel it. Selecting a paint refinement tool commits a live proposal
first.

For Track:

1. Add a parent label to the queue and paint one or more seed masks.
2. Enter inclusive, one-based **Start layer** and **End layer** values. Every
   seed must lie inside the range.
3. Choose the overwrite policy and propagate the selected parent or all queued
   parents.
4. Scrub the affected layers and choose **Confirm** or **Reject**.
5. Press the editor's **Save** after confirming.

Disconnected seed regions are followed as separate internal children and
merged back into the parent label. Track reports inferred children, merges,
terminations, and ambiguous matches. Track prompt Undo/Redo changes prompt
geometry only; queue and child-management actions are not part of that history.
Propagation is a compound pending edit and does not write the working draft.

## Annotation time

The current development version records active annotation time for the assigned
annotator while an eligible editor is open and active. It does not count
read-only viewing, manager inspection, an inactive/hidden browser, or another
user. Heartbeats prevent abandoned tabs from running indefinitely, and
overlapping sessions do not double-count the same wall-clock interval.

Time is cumulative across save, submit, revision, and reopen cycles. A transfer
keeps already recorded time with the person who performed it. Managers can see
task totals and per-person project/dataset/volume breakdowns; annotators can see
their own totals. `-` means the volume predates tracking and its historical total
is unknown, while `0m` means tracking is enabled but no eligible time has accrued.

## Save, submit, and review

Save and submit are separate:

1. Finish edits and press **Save**.
2. Confirm the editor no longer reports unsaved changes.
3. Choose **Submit for review** to create an immutable online snapshot.

Offline label uploads are a separate submission channel. A task may have one
pending online and one pending offline submission. Managers review each channel
independently and choose **Approve & close**, **Approve & keep open**, **Request
revision**, or **Reject**.

Approval installs the chosen snapshot as the official label, voids the competing
pending channel, and starts a fresh working copy from the approved checkpoint.
Request revision and Reject return only the reviewed channel. Source images and
region masks are never changed.

## Sharing and hard cases

Managers can create revocable public links for projects, datasets, and volumes.
Project links open a dataset browser, dataset links open a volume table, and
volume links open the viewer at its captured location. Public pages require no
account and are always marked read-only. Use **Stop sharing** to revoke a link.

In Annotate, make a label active and choose **Record hard case**. Add an optional
primary note, then use the Hard Cases list or detail page for discussion. Project
members can read and reply; the creator and managers can revise the primary note.
Opening the case jumps to a plane containing the label and solos it by default.
Hard-case public links are read-only and independently revocable.

## Practical safeguards

- Save before submitting or leaving important work.
- Confirm the active label and overwrite policy before a whole-volume tool.
- Treat Reset annotations/labels as permanent deletion of the working draft.
- Do not assume a public link grants editing; it never does.
- If AI assistance is unavailable, continue with ordinary tools; no partial
  model result should be persisted.
- Report permission, shape, or pyramid errors to the operator with the project,
  dataset, volume, and layer involved.
