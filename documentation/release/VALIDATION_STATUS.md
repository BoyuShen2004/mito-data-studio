# Current validation status

This page records the latest observed validation state; it is not a claim that
the repository is release-ready. The session ran on 2026-08-23 in the
America/New_York time zone. Some server logs use UTC and therefore show
2026-08-24.

The worktree contained pre-existing development changes. Final release evidence
must be regenerated from a clean, immutable release candidate.

## Passing checks

| Check | Result | Notes |
| --- | --- | --- |
| Frontend unit/component suite | Pass | 82 test files, 543 tests |
| Frontend production build | Pass | TypeScript compilation and Vite build completed |
| Django system check | Pass | No issues reported in the `mito-data-studio` Conda environment |
| Markdown relative-link audit | Pass | Documentation links resolved at audit time |
| Git whitespace check | Pass | `git diff --check` reported no errors |
| Focused tracking suites | Pass | `annotation.test_tracking`: 82 tests; `annotation.test_tracking_branches`: 60 tests |

The frontend build warned that the main JavaScript chunk was approximately
1,037 kB after minification, above Vite's 500 kB warning threshold. This is a
release performance item, not a build failure.

## Open backend validation items

Running `python manage.py test -v 1` discovered zero tests. Explicitly naming
the backend application packages discovered 1,373 tests. That combined run was
stopped after 844 tests because it was long-running; at that point it reported
3 failures, 19 errors, and 40 skips.

Observed categories were:

- the active development environment did not contain `h5py` or `nibabel`,
  although both are present in the release lock file;
- several combined-suite errors reported an already-existing
  `unique_registered_image_per_dataset` constraint, indicating a fixture or
  test-isolation problem that needs investigation;
- two Cellable sidecar recovery assertions expected `verified` but observed
  `edited`;
- one incremental label-summary assertion expected no rescan but observed a
  rescan.

These observations pre-date any claim of a clean release. Reproduce them in the
locked release environment, determine whether each is an environment,
test-isolation, or product defect, and attach the final results to the release
record.

## Required release rerun

1. Create a clean environment from `backend/environment.lock.yml` and
   `frontend/package-lock.json`.
2. Run Django system checks and the explicitly enumerated backend suites.
3. Run the complete frontend suite and production build.
4. Exercise upload, preview, annotation, save, tracking, export, and deletion
   against representative TIFF, HDF5, and NIfTI volumes.
5. Record exact commands, elapsed times, pass/fail counts, hardware, commit SHA,
   and container image digests.
6. Replace this development snapshot with the clean release-candidate result.

See also the [release checklist](RELEASE_CHECKLIST.md) and
[reproducibility record](../paper/REPRODUCIBILITY.md).
