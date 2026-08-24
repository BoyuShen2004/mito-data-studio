# 4. Viewer and volume inspection

[User guide](../user-guide.md) · Previous: [People and assignment](03-people-and-assignment.md) · Next: [Annotation tools](05-annotation-tools.md)

## View versus Annotate

**View** is read-only. **Annotate** mounts the editing controls only when the
current account, assignment, and task state allow changes. Requesters and public
visitors remain read-only. Use View for inspection and review when no draft
change is intended.

The viewer may show a submitted snapshot, the current working label, or the
official approved checkpoint depending on the entry route. Read the page header
and submission context before comparing results.

## Navigate a 3-D volume

- Switch among z, y, and x axes to inspect axial, coronal, and sagittal planes.
- Use the layer control for precise one-based layer navigation; the underlying
  array convention is `(z, y, x)`.
- Pan and zoom to inspect boundaries. A tool-specific cursor may replace pan
  while an editing tool is active.
- **Jump to region** moves to a plane occupied by the ROI when a region mask is
  available.
- Links created from a viewer can preserve axis, coordinates, and active label.

Changing axis changes the displayed plane; it does not transpose or rewrite
the registered data.

## Display controls

Adjust intensity range/contrast for the raw image, label opacity, region-mask
overlay, and layer visibility. Display changes help inspection but do not alter
source pixels or label values.

The labels panel lets you activate an instance, show or hide labels, and solo a
label in dense areas. The active ID is the target used by painting and several
other tools. **Select** (shortcut shown in the UI, default `V`) picks the label
under the cursor. **New** chooses an unused positive instance ID.

## 3-D label inspection

The 3-D panel renders selected label surfaces for spatial context. Use it to
check continuity and relationships, but confirm fine boundaries in orthogonal
2-D planes. Surface smoothing and rendering are visual aids, not a new saved
segmentation.

## Loading and recovery

Large data may arrive as chunks or through a validated streaming pyramid.
Temporary empty tiles, a loading indicator, or a retryable error do not mean the
source was edited. If a problem persists, record the project, dataset, volume,
axis, layer, and whether the image, region, or label layer failed.

## Before editing

Confirm all of the following:

1. correct task and volume;
2. correct source or submission context;
3. axis and layer;
4. active label ID;
5. overwrite policy;
6. region-only state;
7. no unresolved Track preview.

[User guide](../user-guide.md) · Previous: [People and assignment](03-people-and-assignment.md) · Next: [Annotation tools](05-annotation-tools.md)
