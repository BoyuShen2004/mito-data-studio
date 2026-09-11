# 5. Manual annotation tools

[User guide](../user-guide.md) · Previous: [Viewer](04-viewer.md) · Next: [Region and assisted tools](06-assisted-and-track.md)

## Draft, pending edits, and Save

The editable mask on disk is the working draft. New browser edits are pending
in memory until **Save** succeeds. **Undo** and **Redo** operate on pending edit
history. A revision-aware save will not silently clear a newer concurrent edit;
if the save fails or a conflict appears, stop and resolve it before submitting.

**Delete layer** clears every label on the displayed plane only. **Reset labels**
restores the entire task to its registered starting mask and is irreversible.
These controls have very different scope.

## Active label and overwrite policy

Select an existing instance or choose **New** before adding pixels. The
overwrite policy determines whether a tool can write only into background
(`empty only`, the conservative default) or replace existing label voxels
(`overwrite all`). Check it again before a fill, interpolation, or 3-D tool.

## Tool reference

| Tool | How to use it | Main caution |
| --- | --- | --- |
| Select | Click an instance to make its ID active | Confirm the color/ID before painting |
| Brush | Paint the active ID with the selected circular/square footprint | Overwrite policy controls collisions |
| Erase | Clear pixels with the eraser footprint | It writes background, not another label |
| Box Erase | Drag a rectangle to clear an area | The whole rectangle is affected |
| Flood fill | Click a connected region under the cursor | Verify connectivity and configured depth |
| Merge | Choose two label IDs; the result uses the smaller ID | Affects every matching voxel in scope; inspect first |
| Split | Split disconnected 3-D components of one label into new IDs | Intended for disconnected components, not arbitrary boundary drawing |
| Seeds | Place seed points on one target, then run the bounded 3-D watershed split | Review seeds, target ID, ROI protection, and returned preview |
| Interpolate | Paint reviewed masks on start/end layers, set both endpoints, then fill between them | Endpoints are one-based and must bound the intended object |
| Delete | Remove every voxel belonging to the selected ID | Whole-label destructive action |

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

## Suggested editing loop

1. Select or create the intended label.
2. Choose the least destructive tool and conservative overwrite policy.
3. Make a small edit and inspect adjacent layers or an orthogonal axis.
4. Use Undo immediately if the result is wrong.
5. Periodically press **Save** and confirm the unsaved indicator clears.

Tool previews and pending pixels are not a backup. Save before navigation,
submission, long model runs, or closing the browser.

[User guide](../user-guide.md) · Previous: [Viewer](04-viewer.md) · Next: [Region and assisted tools](06-assisted-and-track.md)
