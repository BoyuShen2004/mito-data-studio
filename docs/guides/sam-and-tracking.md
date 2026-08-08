# SAM-assisted masks and tracking

AI assistance is optional. The core Docker image supports ordinary annotation
without model runtimes; Point/Box/Boundary assist and SAM2 Track require an AI
image profile plus the matching Git LFS model files.

## Point, Box, and Boundary assist

- **Point Mask:** click positive prompts; Alt-click negative prompts. Press
  Enter to commit or Escape to clear.
- **Box Mask:** drag a box, review the proposal, then press Enter to commit.
- **Boundary:** place prompts around the intended boundary, then commit the
  proposal.

Committed masks become pending label edits and remain undoable. A proposal is
not saved merely because model inference completed.

## SAM2 Track rail

Track groups prompts by parent label and child candidate. Create seed masks
with the child-class prompt tools, laid out in pairs: Brush | Erase, Box | Box
erase, Point. The **Queued parents** list comes next; beneath it, use the
full-width **Add parent class … to queue** action, then review the derived
Start/End row and add a child as needed.

The Track header's **Undo** and **Redo** buttons apply only to child prompt
geometry (Brush, Erase, Box, Box erase, Point, and committed seed-mask edits).
Queueing or removing a parent and adding, selecting, or removing a child do not
enter that prompt history and are never reversed by those buttons.

Point and Box proposals commit with Enter and cancel with Escape. **Selecting a
paint tool also commits them**: choosing Brush, Erase or Box erase while a green
Box/Point proposal is on screen means "refine this", so the proposal becomes the
child seed first and the brush strokes build on it. Use Escape to throw a
proposal away instead. Switching between Box and Point still discards, because
those are two ways of making a fresh proposal rather than of editing one.

Place seed masks on at least two different z layers. The rail derives Start and
End automatically as the minimum and maximum seed layers, regardless of the
order in which you created them. Review the range, save prompt progress if
needed, choose the **Overwrite** policy directly above the propagation buttons,
then propagate the selected child or all queued parents.

- **Empty voxels only** (default) preserves voxels belonging to other labels.
- **All voxels** allows the propagated Track mask to replace existing labels.

Propagating first commits any live proposal and flushes any in-flight seed
write, so the server always propagates the seeds you can see, not an earlier
version of them.

## Reviewing a propagation

Propagation returns changed planes to the pending buffer as one compound,
undoable edit. It does not persist the working label.

A finished propagation is **pending review**: scrub through the affected layers,
then use **Confirm** or **Reject** at the bottom of the Track rail.

- **Confirm** keeps the propagated planes and retires those parents from the
  queue. The result is still only pending — press the editor's **Save** to write
  it to the working draft.
- **Reject** puts every affected layer back the way it was, as its own undo
  step, and re-arms the parents so you can adjust the seeds and try again.

Editing prompts and propagating again are both blocked until the review is
resolved. Confirm and Reject stay clickable even when the rest of the rail is
disabled — a finished propagation must always be resolvable.

## Runtime notes

- CPU inference works but can be slow, especially for tracking.
- The Docker `ai-gpu` profile needs the NVIDIA Container Toolkit.
- Each gunicorn worker can load its own model copy, so GPU deployments should
  size worker count for available VRAM.
- If the assist is unavailable or times out, ordinary annotation remains
  usable and no partial tool result should be saved.

See [Docker build profiles](../../DOCKER.md#build-profiles) for setup.
