# Mitochondria measurements

Open **Project → Measurements**, select a volume, and enter its actual Z/Y/X
voxel size in nm if missing. **Save voxel size** explicitly updates only that
volume's metadata; it does not run measurements or modify labels. Unsaved
spacing blocks measurement until saved. Volume detail pages link here.

**Mitochondria measurements** offers two explicit
sources: **Official label** (the registered label) and **Saved working draft**.
Managers can queue a run. Users who already have access to the volume can read
its latest result for each source and export CSV. Public shares do not expose
measurement endpoints. Merely opening the page does not start computation.

Each nonzero label ID produces a voxel count, volume in µm³, and TEASAR skeleton
cable length in µm. Voxel spacing comes from the volume's existing nanometre
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
is involved. Deploy the branch's pinned Python requirements, apply that migration,
and reload the web service and restart its processing dispatcher. No feature flags
or annotation lifecycle settings change.

Validation uses synthetic labels and isolated test storage. Never run a production
measurement as part of a development deployment smoke test.
