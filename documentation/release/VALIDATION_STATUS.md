# Current validation status

This page records the latest observed validation state; it is not a claim that
the repository is release-ready. The checks below ran on 2026-09-10
(America/New_York) against `main` at commit
`93aaef3f1fe6525188bf9fe5d44730b07b0f3d37`, in the `mito-data-studio` Conda
development environment on the reference development host, with a clean
worktree. Final release evidence must be regenerated from a clean, immutable
release candidate in the locked release environment.

## Checks run

| Check | Command | Result |
| --- | --- | --- |
| Frontend unit/component suite | `cd frontend && npx vitest run` | Pass — 86 test files, 647 tests |
| Frontend production build | `cd frontend && npm run build` (runs `tsc --noEmit`, then `vite build`) | Pass — entry chunk 340 kB (104 kB gzip), no chunk-size warning |
| Django system check | `cd backend && python manage.py check` | Pass — no issues |
| Backend suite | `cd backend && python manage.py test --noinput` | BACKEND_RESULT |
| Markdown relative-link audit | Script over every tracked `.md` file, checking paths and heading anchors | Pass — no broken links |
| Git whitespace check | `git diff --check` | Pass |

The development environment had the optional readers and model runtime the
backend tests import (`h5py` 3.16.0, `nibabel` 5.4.2, PyTorch 2.5.1 with CUDA
available), so no test module was skipped for a missing package. It is not the
release lock: `requirements-release.txt` pins `nibabel` 5.3.2.

Run the backend suite from `backend/`. Test discovery depends on the working
directory, and a run that reports zero tests is not evidence; see the release
checklist.

## Not covered by this run

- Browser end-to-end tests (Playwright) against a running server.
- The locked release environment and the Docker `core`, `ai-cpu`, and `ai-gpu`
  profiles on clean hosts.
- Manual exercise of upload, preview, annotation, save, Track, export, and
  deletion against representative TIFF, HDF5, and NIfTI volumes.

## Required release rerun

1. Create a clean environment from `requirements-release.txt` (or the matching
   Docker profile) and `frontend/package-lock.json`.
2. Run Django system checks and the backend suite from `backend/`, confirming
   the reported test count is non-zero.
3. Run the complete frontend suite and production build.
4. Exercise upload, preview, annotation, save, tracking, export, and deletion
   against representative TIFF, HDF5, and NIfTI volumes.
5. Record exact commands, elapsed times, pass/fail counts, hardware, commit SHA,
   and container image digests.
6. Replace this development snapshot with the clean release-candidate result.

See also the [release checklist](RELEASE_CHECKLIST.md) and
[reproducibility record](../paper/REPRODUCIBILITY.md).
