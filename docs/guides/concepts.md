# Concepts and data model

Mito Data Studio organizes work as a small hierarchy:

```text
Project
└── Dataset
    └── Volume
        └── Annotation task
```

- A **Project** is the collaboration, review, and access boundary.
- A **Dataset** groups related volume files and their acquisition metadata.
- A **Volume** is one image plus optional region and label volumes.
- A **Task** is the assignable annotation unit. The current product creates at
  most one whole-volume task for each volume and assigns it to one annotator.

## Image, region, and labels

The image layer contains microscopy intensity values. A **region mask** is an
optional binary focus area; it is separate from the editable labels. The label
volume stores integer instance IDs: `0` is empty and each positive number is a
mitochondrion or other annotated instance.

Region masks are never editable from the annotation canvas. **Region only**
uses a region mask to focus display and protect hidden labels; see
[Region only](region-only.md).

## Working draft and official label

Annotation tools change an in-browser pending draft and add Undo/Redo history.
They do not write the working label on disk. **Save** explicitly writes pending
layers to the task's working draft. Submitting presents that saved draft for
review. The official label changes only when a manager approves the submission.

This distinction matters:

- tool result: pending in this browser tab;
- Save: durable working draft;
- Submit: review handoff;
- Approve: official label update.

Closing or reloading a tab with an **Unsaved** status can discard pending work.
