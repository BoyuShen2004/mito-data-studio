# Mito Data Studio

Mito Data Studio is a web application for registering microscopy volumes,
assigning mitochondria annotation work, editing instance labels, and reviewing
results. It provides requester, manager, and annotator workflows in one Django
and React application.

## Quick start with Docker

Docker Compose is the primary path for a fresh clone. It runs the application
and PostgreSQL; Python, Node, and conda are not required on the host.

```bash
git clone https://github.com/BoyuShen2004/mito-data-studio.git
cd mito-data-studio
cp .env.docker.example .env.docker
```

Open `.env.docker` and set the four values in its `REQUIRED` section:
`DJANGO_SECRET_KEY`, `MITO_DB_PASSWORD`, `DJANGO_ALLOWED_HOSTS`, and
`MITO_HOST_DATA_DIR`. Then start the stack:

```bash
docker compose --env-file .env.docker up -d --build
```

Open <http://localhost:8000>. Create the first manager account with:

```bash
docker compose --env-file .env.docker exec app \
  /usr/local/bin/entrypoint.sh manage createsuperuser
```

The default image supports viewing, annotation, review, sharing, and export.
AI-assisted masks and SAM2 tracking are optional profiles; see
[Docker deployment](docs/docker.md#build-profiles).

## Prerequisites

| Path | Required on the host | Use it for |
| --- | --- | --- |
| Docker Compose | Docker Engine 24+ and the Compose plugin | Running the complete application; recommended |
| Conda development | git, git-lfs, conda, and about 10 GB free | Editing code with Django and Vite on the host |
| Optional AI/GPU | Git LFS; NVIDIA driver and Container Toolkit for CUDA | EfficientSAM and SAM2 Track |

`docker-compose.yml` is the complete application stack. In contrast,
`docker-compose.dev.yml` starts only a development PostgreSQL database for a
host conda checkout. Do not run them as if they were the same deployment.

## Documentation

- [Documentation index](docs/index.md)
- [User guide](docs/user-guide.md) — current requester, manager, annotator, sharing,
  tracking, timing, and review workflows
- [Development](docs/development.md) — host setup, daily run, accounts, tests, and
  data safety
- [Docker deployment](docs/docker.md) — portable setup, persistence, GPU, backup,
  and troubleshooting
- [This-host deployment](docs/deployment.md) — maintainer-specific systemd runbook
- [Product invariants](docs/product-invariants.md) — behaviors contributors
  must preserve
- [Contributing](CONTRIBUTING.md), [security policy](SECURITY.md), and
  [changelog](CHANGELOG.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md) and
  [attribution register](docs/attribution.md)

## Roles at a glance

- **Requester:** creates projects, registers datasets, and follows delivery.
- **Manager:** reviews projects, controls access, assigns one volume to one
  annotator, reviews submissions, and manages public shares.
- **Annotator:** works on assigned volumes, explicitly saves draft edits, and
  submits results for review.

Development accounts and the passwordless reset are disabled unless their
explicit development-only flags are enabled. Selecting a development account
fills the login form but never signs in automatically.

## Repository layout

```text
backend/      Django project and domain apps
frontend/     React/Vite application and browser tests
docs/         user, developer, and deployment documentation
scripts/dev/  local setup and live-reload entry points
ops/          container, staging, production, and release assets
vendor/       optional EfficientSAM/SAM2 assets managed with Git LFS
manage.py     repository-wide Django command entry point
Makefile      common setup, run, check, test, and build commands
```

For contribution checks and test commands, start with
[Development](docs/development.md) and [Product invariants](docs/product-invariants.md).

> **License status:** the repository is publicly structured and contributor
> friendly, but its current `LICENSE` grants no permission for first-party code.
> Choose an OSI-approved license before describing or distributing it as open
> source.
