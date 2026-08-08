# Engineering notes and module maps

`progress/` records implementation history, architecture notes, code maps, and
per-package `MODULE.md` files. It is useful to maintainers, but it is not the
end-user manual.

Use these sources consistently:

- [`docs/guides/`](../docs/guides/README.md) is the user-facing source of truth.
- [`README.md`](../README.md) is the short Docker-first landing page.
- [`DOCKER.md`](../DOCKER.md) and [`docs/ops/`](../docs/ops/) are operations
  documentation.
- [`docs/product-invariants.md`](../docs/product-invariants.md) defines product
  behaviors that refactors must preserve.
- This directory explains how the current implementation is organized and why
  earlier engineering decisions were made.

## Start here for engineering work

- [`PROJECT.md`](PROJECT.md) — architecture and repository overview
- [`codemap.md`](codemap.md) — feature-to-code navigation
- [`architecture.md`](architecture.md) — service boundaries and request flow
- [`development.md`](development.md) — historical deep development reference;
  use [`docs/ops/conda-dev.md`](../docs/ops/conda-dev.md) for current setup
- [`api.md`](api.md) — API notes
- [`history/`](history/README.md) — implemented briefs and incident history

Package-level maps live under `backend/*/MODULE.md` and
`frontend/src/*/MODULE.md` as linked from `PROJECT.md`.

## Data is not documentation

Never put runtime volume data here. `MITO_DATA_ROOT` contains registered data,
working label masks, metadata sidecars, and derived artifacts. It must remain
outside documentation and is ignored by Git. Screenshots may use lightweight
PNG/JPEG files; microscopy volumes do not belong in the repository.

## Maintenance rule

When a change alters a package contract, update its module map. Add history
notes when they explain a consequential decision, but update `docs/guides/`
whenever the user-visible workflow changes. This prevents engineering history
from becoming a competing product manual.
