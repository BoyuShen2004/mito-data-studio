# End-to-end operating workflows

For detailed control-level instructions, start with the linked
[module-by-module user guide](../../docs/user-guide.md). This page remains the
short end-to-end operating sequence.

This guide describes the complete current workflow. Controls are permission
aware, so a user may not see actions outside their role or project access.

## 1. Deploy and sign in

For a portable development deployment, follow
[hardware-adaptive deployment](../../docs/hardware-adaptive-deployment.md).
Production operators should additionally follow the security, backup, and TLS
guidance in [Docker deployment](../../docs/docker.md).

Development accounts appear only when explicitly enabled. Selecting one fills
the login form; the user must still press **Sign in**. Production deployments
should disable mock login and the development reset surface.

## 2. Create and approve a project

1. A requester or manager opens **New project**.
2. Enter the scientific title, description, workflow, and requested settings.
3. A manager reviews and activates the project.
4. A manager grants explicit access or assigns a working team.

Project access allows a person to see the project; it does not automatically
assign them an annotation task.

## 3. Register microscopy data

1. Open **Register data** and choose or create a dataset.
2. Enter a server-readable image directory.
3. Optionally enter separate region-mask and editable-label directories.
4. Run **Scan**.
5. Review every proposed image/region/label match and resolve unmatched or
   ambiguous files.
6. Select the correct label type: no label, partial label, or prediction.
7. Register the reviewed rows.

The three layers have different semantics:

- **Image:** immutable intensity data.
- **Region mask:** optional immutable ROI; nonzero means inside.
- **Editable label:** optional starting integer instance segmentation; zero is
  background and positive integers are instance identifiers.

All registered layers for one volume must have the same 3-D shape. The system
does not infer biological channel names or missing voxel sizes. Verify imported
metadata on the volume page before analysis.

Deleting a project, dataset, or volume later removes its dependent work and the
files the application generated for it, including a deleted dataset's folder.
Registered source files are never deleted.

## 4. Build optional streaming derivatives

The original TIFF, HDF5, or NIfTI remains usable. A manager may request an
image or region-mask pyramid from the volume page. A background dispatcher
builds a Zarr v3 derivative, validates deterministic source-derived chunk
checksums, and marks it ready only after validation. A failed build does not
replace or modify the source.

## 5. Assign work

1. A manager opens the project's **Tasks** tab and presses **Assign volumes**.
2. Select one eligible annotator for each volume.
3. Optionally set priority, difficulty, deadline, and instructions.
4. The annotator opens the assigned task from Home → **Assigned to me**.

One volume corresponds to one active task and one assignee. Transfers preserve
already recorded attribution and annotation time.

## 6. Inspect and edit a volume

1. Confirm axis, layer, image contrast, label opacity, active label ID, and
   overwrite policy.
2. Navigate or solo labels before editing dense regions.
3. Use manual tools or an assisted proposal.
4. Inspect the preview, then accept or cancel it.
5. Use Undo/Redo for pending edits.
6. Press **Save** to write the current draft.

Save is explicit. A preview, AI result, or propagation result is not durable
until it passes through the normal confirmation and Save workflow.

### Manual and deterministic tools

| Tool | Operation |
| --- | --- |
| Brush | Paint the active instance ID |
| Erase / box erase | Replace selected label pixels with background |
| Merge | Replace one instance ID with another under the selected policy |
| Split | Separate disconnected components into distinct IDs |
| Flood fill | Fill a connected region from a seed |
| Seeds | Split a bounded 3-D target by watershed from user-provided seeds |
| Interpolate | Generate intermediate masks between two reviewed endpoint layers |
| Delete | Remove every voxel of one label ID |

### Prompted-mask workflow

- **Point:** ordinary clicks are positive; Alt-clicks are negative.
- **Box:** drag a rectangle around the desired object.
- **Boundary:** produces a boundary derived from the prompted mask.
- Press Enter or double-click to commit a proposal; press Escape to cancel.
- Inspect and Save the resulting pending label edit like a manual edit.

### SAM2 Track workflow

1. Add a fresh class to the Track queue.
2. Select a seed tool and draw one or more seed components.
3. Set inclusive **Start layer** and **End layer** values; every seed layer must
   fall inside the range.
4. Choose empty-only or overwrite-all behavior.
5. Run **Propagate selected** or **Propagate all (N)**.
6. Monitor the class count and elapsed timer while the request runs.
7. Scrub the returned canvas preview.
8. Choose **Confirm** to keep the compound pending edit or **Reject** to undo it.
9. Press the editor's ordinary **Save** when satisfied.

Disconnected seed components become automatically inferred branches. The
backend resolves contacts and merges them back into the queued class; users do
not manually create child classes. Review is intentionally based on the canvas
preview rather than a genealogy report.

## 7. Region-only work

When a region mask exists, enable **Only inside region mask** to focus on label
instances touching the ROI and protect outside content from ordinary edits.
Use **Jump to region** to navigate to an occupied plane. The ROI is a read-only
reference layer and never becomes an editable label.

## 8. Record and discuss hard cases

1. Make the relevant label active.
2. Choose **Record hard case** and optionally add a primary note.
3. Open it from the project's **Cases** tab, or from Home, to discuss it with
   project members.
4. Resolve it when the question is settled.

Public hard-case links are read-only and independently revocable.

## 9. Submit and review

1. Save all intended edits.
2. Confirm there are no unsaved changes.
3. Submit an in-app snapshot, or use the separate offline upload channel.
4. A manager inspects the submission and records a decision.

Approval promotes the chosen snapshot to the official label. Revision or
rejection returns that channel for more work. Competing pending channels are
handled explicitly; no submission overwrites registered source imagery.

## 10. Safety checks before leaving a task

- Confirm the editor reports no unsaved changes.
- Resolve any pending Track preview with Confirm or Reject.
- Do not use Reset to solve an ordinary editing mistake; Reset discards the
  working draft and verification state and cannot be undone.
- Treat public links as view access only.
- Report errors with project, dataset, volume, axis, and layer identifiers.
