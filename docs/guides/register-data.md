# Register data

Requesters and managers can register volumes that the server can already read.
Registration records paths; it does not upload or copy source images.

1. Open **Register data**.
2. Select an existing project or create one.
3. Enter a dataset name and the server-side image directory.
4. Optionally enter separate region-mask and editable-label directories.
5. Select **Scan** and review every proposed row.
6. Pair each image with the correct region mask and editable label, if present.
7. Choose the label type, add metadata, stage the dataset, and register it.

Supported scanned image/label sources include TIFF, HDF5, and NIfTI. A
`dataset.json` manifest can prefill dataset metadata. File shapes must agree;
never pair a region or label volume to an image merely because names look
similar.

## The three file roles

- **Image:** immutable intensity source used by View and Annotate.
- **Region mask:** optional focus mask; nonzero means inside the region.
- **Editable label:** optional starting instance mask. Choose `prediction` or
  `partial` when one is present; choose `none` for an unlabeled volume.

Region and label files are separate inputs. A region mask does not become an
editable annotation.

## Streaming pyramids

Registration may queue additive image and region pyramids. The volume remains
usable through its original source while a pyramid builds or if a build fails.
Managers can inspect status and use **Build**, **Rebuild**, or **Retry** on the
volume page. Pyramid creation does not rewrite the source volume.

If registration succeeds but shapes stay blank, ask the operator to check
server read permissions. Directory listing permission alone is not enough; the
service must be able to read each file.
