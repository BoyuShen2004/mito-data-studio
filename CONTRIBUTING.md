# Contributing

Thank you for improving Mito Data Studio. Before starting, read the
[development guide](docs/development.md) and the required
[product invariants](docs/product-invariants.md).

## Development workflow

1. Create a focused branch from the current development branch.
2. Start PostgreSQL with `make db-up` and prepare the checkout with `make setup`.
3. Make a small, reviewable change with tests and user-facing documentation.
4. Run `make check`, `make test`, `make build`, and `make check-git`.
5. Submit a pull request describing behavior changes, migrations, operational
   impact, and the commands used for verification.

Keep Django migrations additive. Never commit `.env`, databases, microscopy
volumes, generated masks, pyramids, logs, model caches, or credentials. Avoid
mixing formatting-only work with behavioral changes.

## Project conventions

- Django apps and tests live under `backend/`; use the root `manage.py`.
- React code and tests live under `frontend/`.
- User, developer, and deployment documentation lives under `docs/`.
- Reusable developer automation lives under `scripts/`; host-specific assets
  live under `ops/`.
- Update `CHANGELOG.md` for changes visible to users or operators.
- Record new copied or vendored code in `THIRD_PARTY_NOTICES.md` and
  `docs/attribution.md` before committing it.

The repository currently grants no license for first-party code. Review
`LICENSE` before copying, distributing, or submitting material.

