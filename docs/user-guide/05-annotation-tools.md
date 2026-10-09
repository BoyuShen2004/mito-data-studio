# 5. Manual annotation tools

[User guide](../user-guide.md) · Previous: [Viewer](04-viewer.md) · Next: [Region and assisted tools](06-assisted-and-track.md)

## Draft, pending edits, and Save

The editable mask on disk is the working label. New browser edits are pending
until a save succeeds. The editor attempts autosave every 30 seconds and when
the tab is hidden, except when Region only would discard recorded outside edits.
This is best-effort: use **Save** and check the unsaved indicator before Submit
or leaving. **Verify** also saves pending planes before verifying a label.

**Undo** and **Redo** change browser edit history. If an edit was already saved,
save the reversal to update disk. Save acknowledges only the revisions it sent;
a newer edit remains pending. On a stale-save conflict the editor reloads the
affected plane and retains non-overlapping pending changes. Overlapping changes
must be inspected and reapplied; a verified-label conflict restores protected
pixels while retaining unrelated edits. A multi-plane save can partially succeed
before an error, so check remaining pending work before retrying.

**Delete layer** asks for confirmation, then clears unprotected label pixels on
the displayed plane only; verified and hidden protected IDs survive. **Reset labels**
restores the entire task to its registered reset seed (updated by approval) and
is irreversible.
These controls have very different scope.

## Active label and overwrite policy

Select an existing instance or choose **New** before adding pixels. **Empty voxels
only** is the default for Interpolate, Flood fill and Track; **All voxels** permits
replacement within their scope. Region only has a separate outside-edit
overwrite control. These controls do not govern ordinary Brush or committed
SAM2 masks: those can replace unverified, visible labels. Erase clears such
pixels regardless of the active ID. Verified labels remain protected in either
mode; Region only additionally protects hidden non-ROI instances.

## Tool reference

| Tool | Scope and action | Inspection / recovery |
| --- | --- | --- |
| Select | Picks the ID under the cursor; changes selection only | Also available in read-only viewing |
| Brush | Circular footprint on the displayed 2-D plane; writes active ID over unprotected pixels | Size is diameter in pixels; size 1 edits one pixel. Undo reverses the stroke |
| Erase | Circular footprint on the displayed plane; writes zero | Active ID does not restrict erasing; inspect before saving |
| Box Erase | Clears unprotected pixels in the dragged 2-D rectangle | Undo reverses; verified/hidden protected content survives |
| Flood fill | Browser computation: 4-connected same-value region in 2-D, or 6-connected axial block with Depth (z) greater than 1 | Empty-only refuses a nonzero seed. Y/X views use depth 1; result enters pending edits |
| Merge | Server returns changed planes for two IDs across the volume; smaller ID survives | Applied as a compound pending edit; inspect and Undo if wrong |
| Split | Server returns a bounded 3-D, 26-connected split of the active ID | Components below 100 voxels are cleared; largest survivor keeps the ID, others receive fresh IDs. All-small targets may disappear; Undo reverses the pending result |
| Seeds (Watershed) | User seeds split one target in a bounded 3-D neighborhood; server returns planes | Inspect the applied pending result and Undo; an oversized global crop may use the seeded neighborhood, while oversized seed spans fail |
| Interpolate | Browser worker computes intermediate masks of the active ID between two nonadjacent endpoint layers in the selected axis | Uses saved plus pending endpoint geometry, in pixel coordinates; endpoints are unchanged. Applies one compound pending edit, then jumps to the middle layer |
| Delete | Removes the active ID across the volume through a server plan | Requires confirmation; applied as pending planes, Undo reverses |
| Undo / Redo | Reverses/reapplies browser strokes or compound tool edits | Does not revert saved disk bytes until the reversal is saved; Track prompt history is separate |

Split/Watershed can preserve their planned changes outside the ROI even with
Region only on; ordinary ROI saves clip outside edits. Merge/Delete and local
fill/interpolation protect hidden and verified content. Backend plan responses
do not write label files. In the editor, deterministic results are applied as
pending edits for inspection and Undo, without a separate Confirm dialog.
Persistence uses the save path, including autosave described above.

Flood fill and Interpolate appear only when the deployment enables them. Point
Mask, Box Mask, and Boundary are described in
[Region and assisted tools](06-assisted-and-track.md).

### Keyboard shortcuts

| Key | Action |
| --- | --- |
| `V` `B` `E` `R` | Select, Brush, Erase, Box Erase |
| `M` `P` `O` | Box Mask, Point Mask, Boundary |
| `T` `C` `G` | Seeds, Split, Merge |
| `I` `L` | Interpolate, Flood fill (when enabled) |
| `A` / `D` | Previous / next layer |
| Arrow keys | Pan the image |
| `F` | Verify the active label |
| `H` | Hide or show verified labels |
| `S` / `Shift+S` | Solo the active label / show all labels |
| `Delete` | Reject the active label — asks first, then removes it from the whole volume |
| `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / Redo (`⌘` on macOS) |
| `Enter` / `Escape` | Commit / cancel a live proposal |

Each tool can also be chosen with `Ctrl` plus a letter (`⌘` on macOS). Those
modified shortcuts are the ones you can change in **Profile**; the defaults use
the letters above, the **Delete** tool has none, and every assignment must be
unique. Brush/eraser size and cursor footprint affect subsequent strokes, so
check them after switching browsers or tools.

## Verify is separate from approval

**Verify** (`F`) first saves all pending planes, then persists per-label verification
state. Verified IDs are protected from painting and tool replacement, including
when **Hide Verified** hides them. **Unverify** removes that protection. A label
must exist in the saved volume; an unused active ID cannot be verified. Verification
is annotator metadata, not a submission or manager approval. Reset and approval
re-seeding start a fresh verification lifecycle.

## Suggested editing loop

1. Select or create the intended label.
2. Choose the least destructive tool and conservative overwrite policy.
3. Make a small edit and inspect adjacent layers or an orthogonal axis.
4. Use Undo immediately if the result is wrong.
5. Periodically press **Save** and confirm the unsaved indicator clears.

Tool previews and pending pixels are not a backup. Save before navigation,
submission, long model runs, or closing the browser.

[User guide](../user-guide.md) · Previous: [Viewer](04-viewer.md) · Next: [Region and assisted tools](06-assisted-and-track.md)
