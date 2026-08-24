# 6. Region-only, assisted masks, and SAM2 Track

[User guide](../user-guide.md) · Previous: [Annotation tools](05-annotation-tools.md) · Next: [Submit and review](07-submit-and-review.md)

## Region-only mode

When an immutable region mask exists, **Only inside region mask** focuses work
on whole label instances that touch the ROI and protects outside content from
ordinary edits. **Jump to region** navigates to an occupied plane. The region
mask remains a reference layer and never becomes an editable segmentation.

Protection is instance-aware, not simply a cropped display. Split and watershed
may return an explicitly bounded 3-D result that must be reviewed. Save remains
explicit and warns when protected outside-region edits would be omitted. Do not
toggle the mode merely to make an unexpected preview disappear; first inspect
which labels touch the ROI.

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

## SAM2 Track workflow

Track propagates prompted instances across an inclusive z-layer range:

1. Make the intended ID active and select **Add class … to queue**. This mints a
   fresh class when appropriate and adds it to Track.
2. Select Brush, Erase, Box, Box erase, or Point in the Track rail and create
   seed geometry. Box/Point proposals require Enter or double-click to commit.
3. Enter one-based **Start layer** and **End layer**. Every seed for that class
   must lie inside the inclusive range.
4. Choose empty-only or overwrite-all behavior.
5. Run **Propagate selected**, or **Propagate all (N)** for every ready queued
   class.
6. Scrub every affected layer and inspect boundaries and contacts.
7. Choose **Confirm** to turn the preview into one compound pending edit, or
   **Reject** to discard it.
8. Press the editor's ordinary **Save** to persist a confirmed result.

**Save progress** preserves committed prompts in the queue and pauses prompt
editing; select a seed tool to resume. Track Undo/Redo changes prompt geometry,
not queue membership and not the editor's ordinary label-edit history.

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
- Confirming Track does not save the task.
- Before overwrite-all propagation, verify class IDs, range, seeds, and ROI
  mode; the operation can affect many planes.

[User guide](../user-guide.md) · Previous: [Annotation tools](05-annotation-tools.md) · Next: [Submit and review](07-submit-and-review.md)
