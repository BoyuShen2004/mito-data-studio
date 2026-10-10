# Contributing

Thank you for improving Mito Data Studio. Before starting, read the
[development guide](docs/development.md) and the required
[product invariants](docs/product-invariants.md).

## Development workflow

1. Create a focused branch from `main`.
2. Start PostgreSQL with `make db-up` and prepare the checkout with `make setup`.
3. Make a small, reviewable change with tests and user-facing documentation.
4. Run checks appropriate to the change: `make check`, frontend tests, backend
   tests from `backend/` (verify nonzero discovery), `npm run build:production --prefix frontend`, and `make check-git`. Documentation-only changes need
   `python scripts/docs/check_links.py` and `git diff --check`.
5. Submit a pull request describing behavior changes, migrations, operational
   impact, and the commands used for verification.

Research-specific project tools belong in the shared **Extensions** catalog.
Follow the [extension contract and checklist](docs/engineering/extensions.md);
Measurements demonstrates the integration. Keep workflow instructions and
developer guidelines in English.

Keep Django migrations additive. Never commit `.env`, databases, microscopy
volumes, generated masks, pyramids, logs, model caches, or credentials. Avoid
mixing formatting-only work with behavioral changes.

## Maintainable workflow changes

- Name the component that owns each draft, selection and server response. Reset
  state when its entity or scientific inputs change; preserve pending input
  through unrelated refreshes. See [frontend refresh and draft ownership](docs/engineering/architecture.md#frontend-refresh-and-draft-ownership).
- Use request inputs as effect dependencies. Stop timers on cleanup and avoid
  overlapping automatic polls or refetching unrelated lists. Keep the task
  serializer prefetch constants required by `AGENTS.md`.
- Cover delayed responses, failures, entity changes and explicit saves where
  they affect the workflow. Check request counts for accidental repeated fetches.
  State performance claims in terms of measured workloads and limits.
- Keep maintained documentation in English. Explain roles, actions, state
  changes and recovery in the user guide; put module paths, extension boundaries
  and known scaling limitations in engineering docs. Describe implemented
  behavior and distinguish it from proposals or unverified capacity claims.

## Role-sensitive changes

Use `accounts.roles.get_role`/`is_manager` for request authorization and existing
scoped querysets. Use `base_role`, `available_roles` or `has_manager_role` when
identifying durable account capabilities or roster membership. Assistant access
is an additional annotator capability, not a replacement base role. Only primary
manager identities may delegate it; never authorize grants through the active
Manager workspace alone. Use the shared authenticated clients for workspace
headers and test forged roles, revocation and both scopes. See
[roles and workspaces](docs/engineering/roles-and-workspaces.md).

## Project conventions

- Django apps and tests live under `backend/`; use the root `manage.py`.
- React code and tests live under `frontend/`.
- All maintained documentation lives under `docs/`; old `documentation/`
  paths are forwarding pages. Start with the [documentation index](docs/index.md)
  and [first contribution guide](docs/getting-started/first-contribution.md).
- Reusable developer automation lives under `scripts/`; host-specific assets
  live under `ops/`.
- Update `CHANGELOG.md` for changes visible to users or operators.
- Record new copied or vendored code in `THIRD_PARTY_NOTICES.md` and
  `docs/attribution.md` before committing it.

The repository currently grants no license for first-party code. Review
`LICENSE` before copying, distributing, or submitting material.
