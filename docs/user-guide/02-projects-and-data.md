# 2. Projects and data registration

[User guide](../user-guide.md) · Previous: [Roles and navigation](01-roles-and-navigation.md) · Next: [People and assignment](03-people-and-assignment.md)

## Create and approve a project

Requesters and managers can select **New project**. Record a meaningful title,
description, annotation target, workflow type, deadline, and any requested
settings. A project is the collaboration and reporting container; a dataset is
a named group of volumes inside it.

A requester-created project waits for manager review. The manager opens its
**Overview** and approves it before assignment. Approval does not assign an
annotator and does not register data.

A project's tab strip is six nouns, and what changes a thing lives at the scope
of the thing it changes:

| Tab | What is there |
| --- | --- |
| **Overview** | Status, deadline, the approval banner, progress, annotator workload, and what is publicly shared (read-only here) |
| **Data** | Datasets and volumes, with one **Add data** button at the top right |
| **Tasks** | The work list for this project. Tick rows and press **Assign volumes** to edit just those; press it with nothing ticked to open the whole project's plan |
| **Cases** | Hard cases raised on this project |
| **People** | Project members and the working team (managers) |
| **Settings** | Edit the project, control its public link, and — in a bordered **Danger zone** at the bottom — delete it |

Other roles see the permitted subset: People is manager-only, and Settings
appears for managers and the requester who created the project.

The header carries the project's name and status and nothing else. If you are
looking for a control, the tab strip is meant to tell you where it is without
your having to hunt.

## Registration references files; it does not upload them

**Register Data** records paths that the server can already read. Paths refer to
the application host or its mounted storage, not the user's laptop. The source
files are not moved or rewritten.

Each volume can contain three layers:

| Layer | Meaning | Mutable in the editor? |
| --- | --- | --- |
| Raw image | Intensity volume used for visual evidence | No |
| Region mask | Optional ROI/focus mask; nonzero means inside | No |
| Editable labels | Optional starting integer instance segmentation | A working copy is editable; the registered source is not |

Supported registration sources include TIFF, HDF5, and NIfTI layouts handled by
the scanner. All three formats are read in on-disk `(z, y, x)` order; NIfTI is
not reoriented from the medical `(x, y, z)` convention. A NIfTI volume
registered before this rule was introduced should be registered again so its
shape is read correctly. All layers paired into one volume must describe the
same 3-D shape.
Label value `0` is background and positive integers are instance IDs. A region
mask is never treated as starting labels.

## Scan and review a directory

1. Select an existing project.
2. Enter **Raw image directory**.
3. Optionally enter distinct **Region mask directory** and **Editable labels
   directory** paths.
4. Select **Scan**.
5. Review every proposed row. Select only intended raw volumes, correct the ROI
   and label pairing with the dropdowns, and optionally use **Edit** to give a
   volume a clearer name.
6. Choose **Label type**: no starting label, partial annotation, or prediction,
   according to the available options and the scientific provenance.
7. Enter the dataset name and any metadata that cannot be derived from files.
8. Select **Register**, or **Add another directory** to queue another dataset
   before registering the batch.

When `dataset.json` is present, the scanner may prefill pairing or metadata. The
badge means the manifest supplied the proposal, not that a human verified it.
Check unmatched, duplicated, or unexpectedly paired filenames before continuing.
Reusing a dataset name adds volumes to that dataset.

## Metadata and physical interpretation

Verify shape, dtype, voxel size, and scientific metadata on the volume detail
page. Do not assume a missing voxel size or channel meaning was inferred. Wrong
physical dimensions can make 3-D display and downstream measurements misleading
even when the pixels look correct.

Managers and requesters with permission can edit descriptive metadata.

## Deleting data

Deleting a project, dataset, or volume also deletes the work that depends on
it: tasks, submissions, discussions, and shares. The confirmation lists what
else will be removed; if annotation work still exists, it says so and asks you
to delete that work as well. This cannot be undone.

Once the delete succeeds, the server also removes the files the application
generated for the deleted item under its data root: working masks, label-state
files, streaming pyramids, model feature caches, Track previews, approved
labels, and submission uploads. Deleting a dataset removes that dataset's
folder; the project's folder stays until the project itself is deleted.
Registered source files — the raw image, region mask, and starting labels you
registered — are never deleted, even when they are stored inside the data root.

## Streaming pyramids

The volume remains viewable from the registered source. Managers can build,
retry, or rebuild optional image and region-mask streaming derivatives from the
volume's **Streaming** section. A background job creates and validates the
derivative before it is marked ready. Pending or failed builds do not replace
the original file; report a failure with project, dataset, volume, and layer.

## Registration troubleshooting

- **Path not found or permission denied:** the server/container cannot read the
  path. An operator must correct the host mount or filesystem permission.
- **Shape mismatch:** pair the correct files or regenerate the layer externally;
  registration cannot safely reshape scientific data.
- **Unexpected pairing:** correct the row manually or use an explicit manifest.
- **No editable label:** register image-only data when appropriate; the working
  label starts empty.

[User guide](../user-guide.md) · Previous: [Roles and navigation](01-roles-and-navigation.md) · Next: [People and assignment](03-people-and-assignment.md)
