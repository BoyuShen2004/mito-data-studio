# Mito Data Studio

Mito Data Studio is a web application for registering microscopy volumes,
assigning mitochondria annotation work, editing instance labels, and reviewing
results. It provides requester, manager, and annotator workflows in one Django
and React application.

## Start here as a new team member

- **Learn the workflow:** [Getting started](docs/getting-started/README.md) → [Glossary](docs/getting-started/glossary.md) → [User guide](docs/user-guide.md).
- **Prepare to develop:** [Code map](docs/engineering/code-map.md) → [Measurements feature walkthrough](docs/engineering/feature-walkthrough.md) → [First contribution](docs/getting-started/first-contribution.md).
- **Browse all documentation:** [Documentation index](docs/index.md). Team members using an existing lab service do not need to run the deployment commands below.

## Quick start with Docker

Docker Compose is the primary path for a fresh clone. It runs the web application
and PostgreSQL; queued work needs a separately started dispatcher. Python, Node,
and conda are not required on the host for the web stack.

```bash
git clone https://github.com/BoyuShen2004/mito-data-studio.git
cd mito-data-studio
cp config/env/docker.env.example .env.docker
ops/docker/detect-hardware.sh --apply .env.docker
```

Open `.env.docker` and set the four values in its `REQUIRED` section:
`DJANGO_SECRET_KEY`, `MITO_DB_PASSWORD`, `DJANGO_ALLOWED_HOSTS`, and
`MITO_HOST_DATA_DIR`. For real-data deployments, also apply the
[no-demo build and aligned feature settings](docs/operations/docker.md#upgrade-profiles);
the template defaults use the development frontend build. Then start the stack:

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
[Docker deployment](docs/operations/docker.md#build-profiles).

Queued pyramid and measurement work needs the [dispatcher](docs/operations/docker.md#queued-processing).
Current Docker dependency profiles omit kimimaro, so nonempty-label measurements
require a separately validated [measurement runtime](docs/engineering/measurements.md#deployment-prerequisites).

## Prerequisites

| Path | Required on the host | Use it for |
| --- | --- | --- |
| Docker Compose | Docker Engine 24+ and the Compose plugin | Running the complete application; recommended |
| Conda development | git, git-lfs, conda, and about 10 GB free | Editing code with Django and Vite on the host |
| Optional AI/GPU | Git LFS; NVIDIA driver and Container Toolkit for CUDA | SAM2 mask tools and Track |

`docker-compose.yml` is the complete application stack. In contrast,
`docker-compose.dev.yml` starts only a development PostgreSQL database for a
host conda checkout. Do not run them as if they were the same deployment.

## Documentation

All maintained documentation lives under [`docs/`](docs/index.md):

| Topic | Entry point |
| --- | --- |
| Onboarding and terminology | [Getting started](docs/getting-started/README.md) |
| Operating the software | [User guide](docs/user-guide.md), including [Measurements](docs/user-guide/09-measurements.md) |
| Code and system design | [Code map](docs/engineering/code-map.md), [architecture](docs/engineering/architecture.md) |
| Local development | [Development](docs/development.md), [product invariants](docs/product-invariants.md) |
| Installation and operations | [Operations](docs/operations/README.md) |
| Releases and research | [Release checklist](docs/release/checklist.md), [research documents](docs/research/README.md) |

Old `documentation/` links remain as forwarding pages. Add new material to
`docs/`, not to a second documentation tree.

Project policies remain at the root: [contributing](CONTRIBUTING.md),
[security](SECURITY.md), [changelog](CHANGELOG.md), [license](LICENSE), and
[third-party notices](THIRD_PARTY_NOTICES.md).

## Roles at a glance

- **Requester:** creates projects, registers datasets, and follows delivery.
- **Manager:** reviews projects, controls access, assigns one volume to one
  annotator, reviews submissions, and manages public shares.
- **Annotator:** works on assigned volumes, explicitly saves draft edits, and
  submits results for review. Members with volume-view access can create volume
  shares; project/dataset shares remain manager-only.

Development accounts and the passwordless reset are disabled unless their
explicit development-only flags are enabled. Selecting a development account
fills the login form but never signs in automatically.

## Repository layout

```text
backend/      Django project and domain apps
frontend/     React/Vite application and browser tests
docs/         onboarding, user, engineering, operations, release, and research docs
documentation/  compatibility links to docs/ (no separate maintained content)
config/env/   environment templates (real .env files stay local)
requirements/ locked Python release dependencies and their input manifest
scripts/dev/  local setup and live-reload entry points
ops/          container, staging, production, and release assets
vendor/       optional SAM2 assets managed with Git LFS
manage.py     repository-wide Django command entry point
Makefile      common setup, run, check, test, and build commands
```

See the [root-file guide](docs/engineering/root-files.md) for why the remaining
configuration files are separate.

For contribution checks and test commands, start with
[Development](docs/development.md) and [Product invariants](docs/product-invariants.md).

> **License status:** the repository is publicly structured and contributor
> friendly, but its current `LICENSE` grants no permission for first-party code.
> Choose an OSI-approved license before describing or distributing it as open
> source.
