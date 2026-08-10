# Annotation tools

Every tool below changes only the pending browser draft. Check the result,
Undo/Redo as needed, then press **Save** explicitly.

| Tool | What it does |
| --- | --- |
| Select | Makes the label under the cursor Active without changing pixels |
| Brush | Paints the Active ID with a circular brush |
| Erase | Replaces editable pixels under the circular brush with `0` |
| Box Erase | Clears editable pixels inside a dragged rectangle |
| Box Mask | Sends a rectangle to the configured segmentation assist, then commits the preview |
| Point Mask | Builds an assisted mask from positive and negative point prompts |
| Boundary | Uses boundary prompts to propose an assisted mask |
| Seeds | Places seeds on one target instance and runs a 3D watershed split |
| Interpolate | Fills one label between two non-adjacent endpoint layers |
| Flood fill | Replaces a connected area in 2D or a limited z depth |
| Split | Splits disconnected 3D components of the Active label into instances |
| Merge | Replaces two chosen label IDs with the smaller ID |
| Delete | Removes every voxel of the selected label from the volume after confirmation |

**Delete layer** is a separate confirmed action that clears editable instances
only on the layer on screen. **Reset labels**, directly beneath it at the end of
the second chrome row, is the whole-task counterpart: it restores the volume's
registered mask across every layer. See
[Annotation basics](annotation-basics.md#reset-labels).

**New** selects the smallest instance ID nothing is using yet — including
unsaved paint and Track parent classes — so it fills the holes Merge, Delete and
Reject leave behind instead of always counting up.

Seeds does not show **Active** or **New**. The first seed selects the target
instance, subsequent seeds must land on it, and Watershed automatically assigns
the smallest safe new IDs using the full saved-plus-pending label set.

## Overwrite policy

Interpolate, Flood fill, and Track propagation share an **Overwrite** policy:

- **Empty voxels only:** write only where the current label is `0`.
- **All voxels:** the tool result can replace existing editable labels.

Region-only protection still applies: a tool cannot alter a label that Region
only hides — that is, one that never touches the region on **any** layer. A
label that is shown stays editable on every layer, including those where it lies
outside the region. See [Region only](region-only.md).

## Assisted mask workflow

For Point Mask and Boundary, add prompts and choose **Commit** or press Enter.
Alt-click supplies a negative point where supported. For Box Mask, drag the
box, review the preview, and press Enter to commit. Escape clears an assisted
preview. An unavailable AI runtime should fail the assist, not the editor.

## Seeds and Watershed

Choose Seeds and place two or more meaningful seeds
within it across the relevant layers. **Run Watershed** computes a bounded
plan and applies the changed planes as one pending, undoable result. Clear
seeds to start again.

When one ID is reused by distant disconnected objects, an oversized global
bounding box falls back to the padded seed neighbourhood. Truly oversized seed
spans are still refused with the Z×Y×X dimensions and bounded voxel limit; the
global safety limit is not raised.

## Interpolate endpoints

Paint the same Active label on two non-adjacent layers. The editor remembers
the most recent usable pair for that label and axis and fills Start/End when
you select Interpolate. It orders endpoints low-to-high. You can also use the
number fields, **Use current**, or right-click the label on two layers and
choose Interpolate. Press Enter or the Interpolate button to run.

## Context menu

Right-click the canvas for Cancel plus the full list: Select, Brush, Erase,
Box Erase, Box Mask, Point Mask, Boundary, Seeds, Interpolate, Flood fill,
Split, Merge, and Delete. Feature-flagged tools appear only when enabled. If
you right-click a label, choosing Interpolate sets that label Active and uses
the current layer as an endpoint. The same menu can Verify, Solo, or Show/Hide
that label in 3D. Right-clicking a row in **Labels** offers row-specific Verify
and Unverify (plus Solo and 3D) without first changing Active. Verification is
applied to saved geometry: if work is pending, Verify saves it first. A Verified
label is then locked against Brush, Erase, Box Erase, Delete layer, Split,
Merge, Watershed, Interpolate, Flood fill, Track, and API writes until the user
explicitly chooses Unverify. Hide Verified changes display only; hidden
Verified voxels remain protected. The metadata sidecar is written atomically
with a checksummed, same-generation backup and survives reopening. If the
primary copy is damaged, the backup is used; if neither copy validates, edits
stop with an error instead of silently treating verified labels as unverified.

**Reset labels** deliberately clears verification together with the discarded
working draft. Approving a submission also establishes a new official
checkpoint and starts a fresh working lifecycle; if a manager later reopens
that task, its labels must be verified for the new round.
