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
[Docker deployment](DOCKER.md#build-profiles).

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

- [User guides](docs/guides/README.md) — role-based product manuals
- [Docker deployment](DOCKER.md) — portable setup, persistence, GPU, backup,
  and troubleshooting
- [Conda development](docs/ops/conda-dev.md) — optional host development path
- [Development checklist](docs/ops/dev-checklist.md) — start the database, run,
  test, and how development differs from production
- [This-host deployment](DEPLOYMENT.md) — maintainer-specific systemd runbook
- [Product invariants](docs/product-invariants.md) — behaviors contributors
  must preserve
- [Engineering documentation](docs/engineering/README.md) — architecture,
  module maps, release records, and design history
- [License](LICENSE)

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
backend/          Django and REST API
frontend/         React/Vite single-page application
docs/guides/      user manuals (the user-facing source of truth)
docs/ops/         operational guides
docs/engineering/ engineering documentation index
ops/              container and host deployment assets
progress/         engineering notes and module maps
vendor/           optional EfficientSAM/SAM2 assets managed with Git LFS
```

For contribution checks and test commands, start with
[Conda development](docs/ops/conda-dev.md) and
[Product invariants](docs/product-invariants.md).

<!-- git-account smoke check: 2026-08-08 -->
