# 6. Region-only, assisted masks, and SAM2 Track

[User guide](../user-guide.md) · Previous: [Annotation tools](05-annotation-tools.md) · Next: [Submit and review](07-submit-and-review.md)

## Region-only mode

When an immutable region mask exists, **Region only** shows whole instances that
have at least one voxel in the ROI anywhere in the volume. Their parts outside
the ROI stay visible; IDs that never touch it remain hidden and protected. This
is instance membership, not an image crop. Verified labels remain protected.
The region overlay is a display layer, never an editable segmentation.

Painting on empty space outside the ROI can still stage pending work. Toggling
Region only does not delete that work. Outside-only fragments stay hidden while
the filter is on. When switched off, its **Overwrite** setting presents outside
edits against stored values: **Empty voxels only** retains existing label voxels;
**All voxels** permits replacement. Inspect this view before saving.

If Region only is on and ordinary outside edits would be lost, **Save** asks
whether to save just inside-region edits. Cancel preserves the pending work;
switch Region only off to save the outside work too. Confirming inside-only
Save omits those outside changes and acknowledges the saved plane, discarding
its omitted pending outside work. Autosave skips this loss-producing case.
Split and Watershed plans are an exception: their reviewed bounded 3-D changes
can be saved outside the ROI without clipping. Inspect them before saving.

**Jump to region** goes to the nearest occupied layer in the current axis. It is
disabled if already on such a layer or if none exists. Region only does not clip
measurement inputs; measurements cover the whole chosen label volume.

## Point, Box, and Boundary proposals

When the optional assisted-mask runtime is available:

- **Point Mask:** ordinary clicks are positive prompts; Alt-clicks are negative.
- **Box Mask:** drag a rectangle around the intended object.
- **Boundary:** creates a boundary-oriented proposal from the prompt.
- Press **Enter** or double-click to commit the live proposal; press **Escape**
  to cancel. Choosing a paint refinement tool may commit the current proposal
  first, so watch the pending-edit indicator.

A committed proposal is still only a pending label edit. Inspect the complete
boundary and neighboring planes, refine it, then use the normal **Save**.
Unavailable AI controls do not prevent manual annotation.

These proposals use SAM2.1 Hiera Large, shared with SAM2 Track. The application
requires CUDA and the configured checkpoint; it does not fall back to SAM2 on
CPU. Missing runtime, weights or GPU makes prompted masks unavailable. Manual
annotation remains usable. The optional **local tracking provider** is a simple
CPU intensity-based stand-in, not SAM2 and not a processing-job backend.

The live proposal is drawn as a translucent green mask with a light-green
outline. Point Mask keeps the part of the prediction that contains your click.
The small tip that follows the cursor only shows where you are pointing; it is
never sent to the model. If a prompt produces no mask, nothing is added — add
another point on the object or try Box Mask.

Right after the server restarts, the model may still be loading. The editor
retries once automatically; if that also fails, one popup says the assistant is
temporarily unavailable. Wait a moment and prompt again.

## SAM2 Track workflow

Track propagates prompted instances across an inclusive z-layer range:

Drag the divider beside Track to resize the panel. Narrow panels stack range
fields and propagation buttons vertically. Scroll within the queue to browse
classes, or within the panel to reach the remaining controls. Confirm and Reject
remain visible at the bottom while the panel content scrolls.

1. Select **Add class … to queue**. It always allocates a fresh unused ID and
   makes it active; it does not reuse the previously active ID.
2. Select Brush, Erase, Box, Box erase, or Point in the Track rail and create
   seed geometry. Box/Point proposals require Enter or double-click to commit.
3. Enter one-based **Start layer** and **End layer**. Every seed for that class
   must lie inside the inclusive range. Drawing on a new seed layer widens the
   range to include it; check the final range before propagating.
4. Choose empty-only or overwrite-all behavior.
5. Run **Propagate selected**, or **Propagate all (N)** for every ready queued
   class.
6. Scrub every affected layer and inspect boundaries and contacts.
7. The preview has already entered the pending buffer as a compound edit.
   **Confirm** keeps it and retires propagated queue entries; **Reject** restores
   the pre-propagation planes as another undoable pending edit and re-arms entries.
8. Press the editor's ordinary **Save** to persist a confirmed result.

Queue entries, committed seed geometry and ranges are stored separately from
label voxels. **Save progress** waits for an in-flight Box/Point proposal and seed
write, commits a nonempty proposal, then pauses prompt editing; select a seed
tool to resume. Empty/failed proposals require correction. It does not save
segmentation planes. Track Undo/Redo restores prompt geometry on the server,
not queue membership or the editor's label history; Confirm clears prompt history.

Propagation runs within the HTTP inference request, not a durable processing
job. It proceeds both directions from seeds within inclusive z bounds, using
bounded XY windows; widely separated seeds can use separate windows. Earlier
queued classes win protected collisions. Crop/slab limits can reject a request;
reduce range or separate work and inspect crop-edge truncation.

## Branch behavior

Disconnected components of one seed are followed as separate inferred branches
and merged back into the queued parent label. Draw separate seed blobs when an
instance has separate visible pieces; users do not manually create branch
objects. Review happens on the canvas preview. The rail does not promise a
biological lineage or branch-genealogy report.

## Failure and safety rules

- Do not leave the editor with an unresolved Track preview; choose Confirm or
  Reject.
- A timeout or model error does not make a partial result authoritative. Retry
  or continue with manual tools.
- Confirm itself does not save label voxels. However, current autosave checks do
  not exclude unresolved Track previews from the pending buffer. A timer or
  hidden-tab flush may save them before Confirm. Resolve previews promptly;
  after Reject, **Save** the restored planes and check success. This suspected
  persistence bug is recorded in the [audit](../research/documentation-audit.md#ambiguities-and-suspected-implementation-bugs-no-runtime-changes).
- Before overwrite-all propagation, verify class IDs, range, seeds, and ROI
  mode; the operation can affect many planes.

[User guide](../user-guide.md) · Previous: [Annotation tools](05-annotation-tools.md) · Next: [Submit and review](07-submit-and-review.md)
