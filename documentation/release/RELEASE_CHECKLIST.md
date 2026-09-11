# Release checklist

## Current status

The repository has a release-oriented structure, locked dependencies, Docker
profiles, tests, security guidance, third-party notices, and the documentation
portal. It is **not yet legally or scientifically ready for public release**
solely by virtue of this reorganization.

The root `LICENSE` currently says “No License Granted.” The owner must choose
and approve a distribution license before describing the first-party software
as open source or granting reuse rights. Do not change that decision implicitly.

## Required before a software release

- [ ] Select and review the first-party license.
- [ ] Confirm third-party notices and vendored model hashes/licenses.
- [ ] Audit the provenance and redistribution terms of the Cellable-derived
      modules under `backend/annotation/cellable_port/`, each marked by a
      one-line provenance note.
- [ ] Create a clean release branch/commit with no runtime data or secrets.
- [ ] Set one consistent semantic version across changelog, frontend package,
      image tags, service assets, and release notes.
- [ ] Run `make check`, backend/frontend tests, production build, and
      `make check-git` from the release environment.
- [x] Review or split the main JavaScript bundle if it remains above the
      configured 500 kB warning threshold — the entry chunk measured 340 kB on
      2026-09-10, with routes loaded lazily; re-measure at release.
- [ ] Make the default backend test command discover the intended suite, or
      document and enforce the explicit Django app list in CI; a zero-test run
      is not release evidence.
- [ ] Run dependency vulnerability and license inventories.
- [ ] Validate CPU and GPU Docker profiles on clean hosts.
- [ ] Exercise backup/restore, migration, rollback, health, and public-share
      verification procedures.
- [ ] Confirm model weights are real Git LFS objects in the release artifact.
- [ ] Remove generated databases, caches, logs, local env files, and build junk.
- [ ] Publish known limitations and security/contact information.
- [ ] Create an immutable signed tag and record container image digests.

## Required before a Scientific Reports submission

- [ ] Freeze the exact software tag used for all reported results.
- [ ] Create `CITATION.cff` only after author order, title, version, repository
      URL, license, and preferred citation are approved.
- [ ] Complete study-specific Methods fields in `paper/METHODS.md`.
- [ ] Archive datasets/checksums and an executable analysis environment.
- [ ] Validate segmentation accuracy against a defined reference standard.
- [ ] Report sample sizes, exclusions, uncertainty, and statistical methods.
- [ ] Benchmark all claimed deployment profiles under a prespecified protocol.
- [ ] Distinguish the reference development server from minimum requirements.
- [ ] Complete data/code availability, ethics, consent, author-contribution,
      funding, and competing-interest statements.
- [ ] Verify every model/software citation from its primary source.

## Release acceptance evidence

Store the final test reports, browser verification results, hardware probe,
sanitized configuration, migration output, dependency inventory, model hashes,
image digests, release notes, and rollback result together with the tag. A
checkbox without archived evidence is not a reproducible release result.

The latest development audit, including unresolved checks, is recorded in
[Current validation status](VALIDATION_STATUS.md). Replace it with evidence
from a clean release candidate before publication.
