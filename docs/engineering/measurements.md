# Mitochondria measurements — implementation reference

For a first run, read the [user guide](../user-guide/09-measurements.md).
For the request-to-runner code path, read the [feature walkthrough](feature-walkthrough.md).

Open **Project → Measurements**, select a volume, and physical spacing is read automatically: registered values take priority,
then missing axes are read from the raw image header. TIFF/OME/ImageJ, NIfTI
with declared spatial units, and HDF5 `element_size_um` are supported by the
existing application readers. Unitless pixel ratios are not physical spacing.
If metadata is unavailable or incomplete, enter the actual missing Z/Y/X values
in nm. Detection does not write metadata, and complete detected spacing can be
used immediately. **Save voxel size** explicitly updates only that
volume's metadata; it does not run measurements or modify labels. Unsaved
spacing blocks measurement until saved. Volume detail pages link here.

**Mitochondria measurements** offers two explicit
sources: **Official label** (the current official reference, initially registered
labels) and **Saved working draft**.
Managers can queue a run. Users who already have access to the volume can read
its latest result for each source and export CSV. Public shares do not expose
measurement endpoints. Merely opening the page does not start computation.

Each nonzero label ID produces a voxel count, volume in µm³, and TEASAR skeleton
cable length in µm. The volume/header storage contract is µm; the UI and skeleton engine use nm.
The measurement boundary converts µm to nm exactly once. Voxel spacing comes from
registered values or supported source-file
metadata in **Z, Y, X** order; missing, nonpositive or nonfinite spacing blocks
a run. Cable length is the sum of skeleton edge lengths, not end-to-end extent.
The method excludes connected components smaller than 100 voxels from the
skeleton, but counts them in volume. Zero cable length can therefore mean that
no skeleton survived the threshold. Labels sharing an ID are measured together.

Measurements cover the **whole label volume**, without task-range or ROI
clipping. They neither change labels nor save/submit/approve annotations.
Unsaved browser edits and submission snapshots are not measurement inputs.

The existing processing dispatcher executes `measure_mito` jobs; requests only
queue work. Only one queued/running measurement is allowed per volume. A source
file identity/size/timestamp and voxel-spacing check before and after execution
rejects changed inputs rather than publishing mixed results. Completed results
are marked historical when their inputs no longer match. CSV preserves the run,
source, spacing, threshold, timestamp and full-precision values.

Web runs limit each padded label crop to 8,000,000 voxels to bound skeletonization
memory. A larger crop fails the run explicitly without partial results. Use the
existing offline command for those volumes:

```sh
python manage.py measure_mito --volume 12 --out mitochondria.csv
python manage.py measure_mito --volume 12 --working --out draft.csv
```

Results are derivative JSON artifacts under `MITO_DATA_ROOT/processing_jobs/`.
Migration `processing.0003` only adds a job-type choice; no domain/data migration
is involved. Deploy the pinned Python requirements, apply that migration,
and reload the web service and restart its processing dispatcher. No feature flags
or annotation lifecycle settings change.

## Header detection caveat

Header reads are cached by path and modification time (`core/utils.py`). Rapid
in-place source rewrites that preserve that timestamp can return stale spacing,
including unknown values from an earlier header. Keep registered sources stable;
a maintainer can invalidate the process header cache or restart the process after
an external replacement. See [audit B09](../research/documentation-audit.md#english-documentation-follow-up--2026-10-09)
for the reproduced limitation and intermittent regression-test failure. This
cache caveat does not supply missing physical calibration.

## Export and provenance limits

Browser CSV columns, in order, are:

```text
volume_id,run_id,source,measured_at,voxel_size_z_nm,voxel_size_y_nm,voxel_size_x_nm,dust_size_voxels,label_id,voxel_count,volume_um3,skeleton_length_um
```

Browser export uses unrounded numeric values from the result. The offline command
exports only `label_id,voxel_count,volume_um3,skeleton_length_um`, with six
significant digits for physical values. It runs synchronously without a
ProcessingJob, web crop cap or before/after fingerprint check; keep inputs stable
and archive spacing/source/command separately.

Job config records source path, device/inode/size/mtime/ctime and effective spacing;
result JSON records timestamp, source, spacing, dust threshold, method and scope.
These are metadata fingerprints, not content hashes. CSV does not include the
full TEASAR parameters, software/dependency version or review-approval evidence.
The latest job per source is shown, so a failed newer run does not automatically
fall back to an older successful run. Changed input during execution fails without
publishing partial results; a completed stale result remains explicitly historical.

The padded crop spans all components sharing an instance ID, even distant ones;
the 8,000,000-voxel limit applies to its bounding-box volume, not foreground count.
The full label scan and input reader can also consume memory: this cap is not a
guarantee of total process memory. TEASAR uses the vendored helper's fixed parameters
(`scale=1.5`, `const=500`, `max_paths=50`, branching/border fixes); retain exact
`annotation/third_party/em_erl_skel.py` and kimimaro version for reproducibility.

## Deployment prerequisites

The web API pins measurement jobs to the local processing backend regardless of
`MITO_PROCESSING_BACKEND`; SAM2/GPU and SLURM are not used for skeletonization.
The dispatcher must run with kimimaro and dependencies installed. The release lock
pins kimimaro 5.8.5 and the conda manifest includes it. Current Docker profile
manifests omit it, and Compose does not start a dispatcher. Stock images therefore
do not provide a working measurement runtime for nonempty labels. Use a separately
validated release/conda measurement environment; do not treat HTTP queue success
as proof that a container can compute results. This configuration gap is recorded
in the [audit](../research/documentation-audit.md#ambiguities-and-suspected-implementation-bugs-no-runtime-changes).

Validation uses synthetic labels and isolated test storage. Never run a production
measurement as part of a development deployment smoke test.
