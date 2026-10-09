# Environment templates

This directory contains example configuration only. Active local files remain
at the repository root as `.env`, `.env.docker` or `.env.docker.dev`, and Git
ignores them. Do not put real secrets or production configuration here.

Choose one environment from the repository root, and copy its template only if
the destination does not already exist:

| Environment | Template → local file | Entry point |
| --- | --- | --- |
| Conda with local Django/Vite | `config/env/host.env.example` → `.env` | `make setup` / `make dev` |
| Docker web/database deployment | `config/env/docker.env.example` → `.env.docker` | `docker compose --env-file .env.docker …` |
| Docker development web/database stack | `config/env/docker-dev.env.example` → `.env.docker.dev` | `make docker-dev-up` |

These templates target different runtime setups. Preserve their separate
identities and defaults. Moving templates did not change their fields or values.
See [development](../../docs/development.md) and
[Docker deployment](../../docs/operations/docker.md) for configuration steps,
aligned feature flags and separately started processing dispatchers.
