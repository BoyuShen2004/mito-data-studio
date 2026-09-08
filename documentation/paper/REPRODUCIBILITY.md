# Reproducibility record

## Software snapshot

At documentation capture on 2026-09-08:

- branch: `main`;
- base commit: `90df543dfb579edb42464813c20fa2414f810773`;
- worktree: **clean** — no uncommitted changes;
- frontend package version: 1.1.5;
- production baseline noted by the changelog: 1.1.5;
- annotated tags in this repository: **none**.

The tree being clean is necessary but not sufficient. `1.1.5` is a package
version string that has been reused across many commits, so it does not
identify software: the commit above does. **There is still no immutable tag.**
Create a release commit and an annotated tag, and cite that tag together with
its commit hash, before running anything intended for a manuscript. Without a
tag, a reader cannot retrieve the exact software a figure came from.

An earlier capture (2026-08-23, commit `69faf4a8…`) recorded a *modified*
worktree. Do not cite that snapshot: the interface, several API querysets, and
the label-artifact file mode have all changed since.

## Environment authorities

| Artifact | Purpose |
| --- | --- |
| `requirements-release.txt` | Locked Python release dependencies and hashes |
| `frontend/package-lock.json` | Locked JavaScript dependency graph |
| `environment.yml` | Conda development/AI environment |
| `ops/docker/requirements-*.txt` | Core, AI-CPU, and AI-GPU image profiles |
| `Dockerfile` and Compose files | Container build and runtime topology |
| `.env.docker*.example` | Documented configuration surface without secrets |
| `THIRD_PARTY_NOTICES.md` | Vendored component revisions, licenses, and hashes |

## Experiment archive checklist

For every manuscript figure/table or performance run, archive:

1. clean git commit and tag;
2. `git status --short` proving the experiment tree state;
3. dependency lock files and container image digest;
4. sanitized effective configuration and hardware-probe output;
5. source-data identifiers, checksums, shapes, dtype, axes, voxel sizes, and
   units without exposing protected data;
6. model checkpoint/config hashes;
7. random seeds and deterministic/non-deterministic framework settings;
8. command or UI protocol, operator, repetitions, inclusion/exclusion rules;
9. raw `mito.ai.timing` / `mito.track.timing`, resource telemetry, and errors;
10. analysis scripts, raw results, statistical outputs, and figure generation;
11. test report and migration state;
12. approvals for data, ethics, authorship, licensing, and public release.

## Suggested verification commands

```bash
git rev-parse HEAD
git status --short
ops/docker/detect-hardware.sh
docker compose version
docker compose --env-file .env.docker config --quiet
python manage.py check
make test
make build
make check-git
```

Run model benchmarks inside the same container/profile used for the study.
Record `nvidia-smi` before, during, and after each run and distinguish model-load
time from warm inference.

## Model references

- EfficientSAM: Xiong et al., arXiv:2312.00863,
  <https://arxiv.org/abs/2312.00863>.
- SAM 2: Ravi et al., arXiv:2408.00714,
  <https://arxiv.org/abs/2408.00714>.

Generate the final journal bibliography from the official BibTeX records and
verify author order, venue, year, and identifiers at submission time.

