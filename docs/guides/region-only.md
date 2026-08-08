# Region only

**Region only** appears when a volume has a region mask. It is a focus and
protection mode; it does not edit the region mask.

## Display rule

Membership is decided **over the whole volume**, not layer by layer: a label is
visible if at least one voxel of it touches the region on **any** layer. Such a
label is then shown whole on **every** layer, including the layers where it sits
entirely outside the region. A label that never touches the region anywhere is
hidden everywhere.

This is what a mitochondrion needs: one 40-layer object that enters the region
on five of those layers is one object, and hiding it on the other 35 showed a
single mito as a handful of disconnected fragments.

Painting outside the region does not create a visibility exemption. Switching
Region only off and on, or undoing an outside stroke, always returns to the
same strict touch-based display.

Unsaved work counts immediately: a label you have just drawn inside the region
appears as soon as the stroke touches it, without waiting for a Save — the
browser answers for the layer on screen, and the server answers for the rest of
the volume.

The server half is one scan of the region's own layers, cached per volume, so
scrubbing z and painting never trigger a rescan. It is deliberately **not**
recomputed on every Save: instead, use **Refresh** in the Labels panel when you
want it re-derived (Reset labels does it automatically). Between refreshes the
only staleness possible is a label that has been erased out of the region still
being shown — Region only showing one label too many is recoverable; hiding one
you are working on is not.

Use **Jump to region** to move to the nearest layer containing region pixels.
The region index is prefetched in the background when the viewer opens.

## Hiding non-region labels in the lists

When a volume has a region mask, a **Hide non-ROI** checkbox sits to the right
of **Hide Verified** in the Labels panel. It filters the **Labels** list (both
This layer and All) and the **3D Labels** view down to the labels Region only
would show — the same volume-wide membership described above.

It is independent of the Region only toggle: you can scope the list to the
region while still painting with the whole volume visible on the canvas. It is
off by default and lasts for the session.

A label the server has not seen yet (unsaved paint) is never hidden by it — an
id missing from the summary is unknown, not proven to be outside the region.

## Edit protection

While Region only is on, hidden labels are protected:

- painting another ID over a hidden label leaves it unchanged;
- a label that *is* shown stays fully editable on every layer, including the
  layers where it lies outside the region — visibility and editability use the
  same membership set, so Region only never silently reverts a stroke on
  something it is displaying;
- Erase, Box Erase, Flood fill, and other tools cannot clear or replace it;
- empty pixels outside the region may receive staged paint, but that paint does
  not bypass the strict display rule.

This prevents an annotator from modifying an instance they cannot see.

## Leaving Region only

The top-bar **Overwrite** choice decides how staged outside-region edits are
presented when Region only is switched off:

- **Empty voxels only:** keep baseline labels and accept outside paint only
  where the baseline was empty.
- **All voxels:** outside edits win at every pixel they changed.

Undo keeps this staging record aligned with the pending label buffer, so an
undone outside stroke is not projected later.

## Saving while focused

Save remains explicit. If Region only is still on, outside-region pending paint
is protected from the disk write. When that would omit work, the editor asks
whether to save only inside-region edits and explains that switching Region
only off first is required to save the outside work too.
