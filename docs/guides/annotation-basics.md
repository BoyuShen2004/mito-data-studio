# Annotation basics

Open **Annotate** from an assigned task or volume. The editor contains the
image canvas, label overlay, tool strip, tool-specific controls, Labels panel,
and optional Track rail.

## Navigate the volume

- Use **Axis** to view `z`, `y`, or `x` planes.
- Scrub the layer control or press `A`/`D` for the previous/next layer.
- Use the arrow keys to pan; use the canvas zoom controls to zoom or fit.
- Adjust brightness/contrast without changing source data.
- When a region mask exists, **Jump to region** moves to the nearest plane
  containing region pixels. It does nothing if the current plane already has
  region content; equal-distance ties choose the lower layer.
- **Region only** focuses the label display; see [Region only](region-only.md).

Switching axis changes the plane coordinate system. If that would discard
pending edits on the current axis, the editor asks for confirmation.

## Active labels and visibility

**Active** is the positive instance ID used by paint and several tools. Select
an existing label on the canvas, enter an ID, or choose **New**. Labels can be
hidden, soloed, verified, and pinned for 3D without changing their voxels.
Verified is also an edit lock: choose **Unverify** before changing that label's
shape. Hiding verified labels cannot make them paint-through targets.

**Select** on the canvas behaves exactly like clicking a row in the Labels
panel: it makes the label Active, and — while the panel is scoped to **All** —
also jumps to the layer that label starts on. Scoped to **This layer**, it
selects without navigating. In either scope, the scrolling row region reveals
the selected label without changing its filter or sort position; the
Labels/filter/search chrome stays fixed. Label rows and Filters Options do not
have their own destructive action; use the toolbar Delete tool or the label's
right-click Delete action.

**New** picks the **smallest instance ID nothing is using yet**, counting labels
already in the volume, unsaved paint in your pending layers, and IDs reserved as
Track parent classes. It fills holes rather than always counting up: with 1–101
and 103–115 in use, New gives 102; once 102 is occupied, New gives 116. Clicking
New repeatedly without painting keeps returning the same ID.

Right-click opens the complete tool menu, laid out in two columns of related
pairs (Brush | Erase, Box Mask | Box Erase, Split | Merge). When the cursor is
over a label, the menu also offers label-specific Verify and Solo actions.

## Save, Undo, and Redo

All annotate tools—including Split, Merge, Watershed, Interpolate, Flood fill,
Delete, and Track propagation—apply to pending browser memory and add undo
history. Multi-layer operations are one compound Undo entry.

1. Run a tool and check the result.
2. Use **Undo** or `Ctrl/Cmd+Z` to reverse it; use **Redo** or
   `Ctrl/Cmd+Shift+Z` to restore it.
3. Confirm the status says **Unsaved**.
4. Press **Save** to write every pending layer to the working draft.

Save is the only normal annotation action that persists the pending label
buffer. Wait for Save to finish before reloading or closing the tab. Saving a
draft is not the same as submitting it for review.

## Reset labels

Two controls throw annotation away, at two scales. They sit at the right-hand
end of the two chrome rows, **Reset labels** directly below **Delete layer**:

- **Delete layer** clears every label from the layer on screen. Other layers are
  untouched, and it is an ordinary pending edit — Undo reverses it, and nothing
  reaches disk until Save.
- **Reset labels** discards this task's **whole working annotation** and
  restores the volume's **registered** label mask. It affects every layer, saved
  and unsaved, and also clears the Track prompt queue and per-label verification
  state. It asks for confirmation first, writes to disk immediately, and
  **cannot be undone**.

Reset labels never changes the registered source mask — that file is only read.
Think of the working annotation as a draft forked from the registered mask;
Reset throws the draft away and forks a fresh one.

A manager can do the same thing for someone else's task from the **Assign**
area; see [Tasks and assignment](tasks-and-assignment.md). Reset is refused on a
task that has been approved and locked — a manager reopens it first.
