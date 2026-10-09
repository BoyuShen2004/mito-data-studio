# Root files and their purpose

The root retains tool entry points and project policies. Environment templates
live in `config/env/`; release dependencies live in `requirements/`. Files on a
similar topic can serve different tools and need not be duplicates.

| File | Purpose |
| --- | --- |
| `README.md` | Repository entry point linking to user and developer documentation |
| `LICENSE`, `THIRD_PARTY_NOTICES.md` | First-party licensing status and separate third-party obligations |
| `SECURITY.md` | Security reporting policy discoverable by repository hosting tools |
| `CONTRIBUTING.md`, `CHANGELOG.md` | Contribution workflow and version history |
| `AGENTS.md` | Repository requirements for coding agents |
| `.gitignore`, `.dockerignore` | Git tracking and Docker build-context exclusions respectively |
| `.gitattributes` | Git LFS rules for model weights |
| `.editorconfig` | Shared editor formatting settings |
| `pyproject.toml` | Optional pytest/coverage configuration; not a dependency lock |
| `manage.py` | Root Django command entry point used by scripts and documentation |
| `Makefile` | Common development, build and check commands |
| `environment.yml` | Conda development environment including Python, Node and CUDA dependencies |
| `Dockerfile` | Three-stage container build |
| `docker-compose.yml` | Web application and database deployment; dispatcher started separately |
| `docker-compose.dev.yml` | Development database only |
| `docker-compose.dev-stack.yml` | Development web/database stack with separate identity and storage |
| `.env` (untracked) | Active local configuration; not disposable temporary data |

The three Compose files have different database identities, volumes, ports and
development behavior. Preserve their distinctions when reorganizing files so
that login, feature gates and deployment data stay consistent.

## Files moved to dedicated directories

| Previous path | Current path |
| --- | --- |
| `.env.example` | [config/env/host.env.example](../../config/env/host.env.example) |
| `.env.docker.example` | [config/env/docker.env.example](../../config/env/docker.env.example) |
| `.env.docker.dev.example` | [config/env/docker-dev.env.example](../../config/env/docker-dev.env.example) |
| `requirements-release.in` | [requirements/release.in](../../requirements/release.in) |
| `requirements-release.txt` | [requirements/release.txt](../../requirements/release.txt) |

The previous paths do not retain duplicate copies. Repository scripts and docs
use the current paths; update external scripts if they still reference old
locations. Real `.env` locations and runtime loading behavior are unchanged.

`make help` lists development commands. `make build` includes the development
account build flag; use `make build-production` for production.
`make test-backend` runs from `backend/`, avoiding root discovery of zero tests.
Always check the discovered test count.
