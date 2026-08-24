# Changelog

Notable user-facing and operational changes are recorded here. This project
follows semantic versioning for tagged releases.

## Unreleased

### Changed

- Added a release/publication documentation portal covering product behavior,
  complete user workflows, architecture, data contracts, AI models, reference
  hardware, manuscript methods, reproducibility, and release gates.
- Reorganized the repository around a root Django `manage.py`, `docs/`,
  `scripts/dev/`, and a common `Makefile` command surface.
- Kept `backend/manage.py` as a temporary compatibility shim for installed
  service units and existing automation.
- Consolidated current user, development, Docker, and host-deployment guidance.
- Added contribution, security, editor, attribution, and repository-layout
  documentation.

## 1.1.5

- Current production release baseline.
