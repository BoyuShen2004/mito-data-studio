# 9. Measurements: analyze saved labels

Open **Project → Measurements** and select a volume. Managers can start a run;
authenticated users with volume-view access can read results and export CSV.
Public shares do not expose measurements.

## Run measurements

1. Select a volume and wait for physical voxel size detection. Registered values
   take priority; supported source-file metadata supplies missing axes.
2. Check Z, Y and X in **nm**. Obtain missing values from acquisition records;
   do not guess. After manual changes, press **Save voxel size**.
3. Choose **Official label** or **Saved working draft**. The latter excludes
   pending edits still held only in the browser.
4. Press **Run measurements** and wait for the processing job to finish.
5. Inspect results per label ID or export CSV. Changing volume or source selects
   that combination's latest job and result.

Complete detected spacing can be used immediately. Detection does not write
registered metadata. **Save voxel size** updates spacing metadata only;
measurements do not save labels, submit work or approve tasks. Saving voxel size
or refreshing the volume list keeps your selected measurement source. Unrelated
list refreshes also keep pending spacing edits. Changing to another volume
starts its own setup. After spacing changes, results are fetched again so their
current or outdated status reflects the saved metadata.

The official label is the current reference: it may be an initial registered
label, an approved submission snapshot or a saved draft promoted during
[working-team withdrawal](03-people-and-assignment.md#working-team-changes-and-withdrawal).
The name does not guarantee review approval. Submission snapshots cannot be
selected directly as measurement sources; approval must first install a
snapshot as the official label, or use an existing official/working file.

Only one measurement may be queued or active per volume, including across the
two sources. If inputs change before or during execution, the job fails without
publishing partial results. Ensure label saves have finished before retrying.

## Understand the results

- **Voxel count:** number of voxels with this ID.
- **Volume (µm³):** voxel count multiplied by calibrated voxel volume.
- **Skeleton cable length (µm):** total length of skeleton edges, rather than
  the straight-line distance between the object's ends.

For example, with Z/Y/X spacing of 30/16/16 nm, 100 voxels occupy
`100 × 0.03 × 0.016 × 0.016 = 0.000768 µm³`.
This illustrates unit conversion; it is not a recommended spacing value.

Measurements cover the whole label volume without ROI or task-range clipping.
Disconnected regions sharing one ID are measured together. Components smaller
than 100 voxels count toward volume but are excluded from skeletonization, so
zero cable length does not necessarily mean zero volume.

## Recover from failures or outdated results

| Symptom | Check / recovery |
| --- | --- |
| Missing spacing or disabled Run | All three axes need real positive spacing; save manual changes and check that your account is a manager |
| No label available | Check that the chosen source exists and the working draft has been saved |
| Stays queued or Measurement failed | Ask the maintainer to check dispatcher job types and kimimaro dependencies. Current Docker images omit kimimaro and Compose starts no dispatcher. Repeatedly queueing work will not fix this |
| Crop limit exceeded | Each padded object bounding box in the web path is limited to 8,000,000 voxels. Ask the maintainer to assess offline measurement |
| Outdated result | Labels or spacing changed. Verify the source and run again |

For method limits, provenance and offline commands, see the
[measurement implementation](../engineering/measurements.md).

[User guide](../user-guide.md) · Previous: [Collaboration and safety](08-collaboration-and-safety.md)
