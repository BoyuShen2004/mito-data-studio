# Conda development setup

This is the secondary setup path for contributors who want Django and Vite on
the host. For a clone-to-running deployment, use [Docker](../../DOCKER.md). For
the condensed ordered commands, see the
[development checklist](dev-checklist.md).

## Prerequisites

- git and git-lfs
- conda or mamba
- about 10 GB free for the environment and optional model assets
- optional NVIDIA GPU compatible with the CUDA pin in `environment.yml`

## Fresh environment

```bash
git clone https://github.com/BoyuShen2004/mito-data-studio.git
cd mito-data-studio
git lfs install
git lfs pull
conda env create -f environment.yml
conda activate mito-data-studio
./dev-setup.sh
./dev-launch.sh
```

Open <http://localhost:5173>. `dev-launch.sh` starts Django and Vite and stops
both with Ctrl+C.

`dev-setup.sh` never overwrites an existing `.env`. By default it checks the
environment, prepares missing local state, installs frontend packages only when
needed, and applies additive migrations. It does not reshape a mature conda
environment or install Python dependencies unless asked.

Useful variants:

```bash
./dev-setup.sh --install-deps  # install missing light pip dependencies
./dev-setup.sh --smoke         # load runtimes/weights and build the frontend
./dev-setup.sh --check-git     # reject tracked secrets, DBs, or volume data
```

To intentionally synchronize the complete environment:

```bash
conda env update -f environment.yml --prune
```

The environment pins `mkl<2024` because newer MKL combinations can break the
pinned CUDA PyTorch build.

## Development database

`.env.example` defaults to PostgreSQL on port 5433. Start only that database
with:

```bash
docker compose -f docker-compose.dev.yml up -d
```

This file is not the application deployment. `docker-compose.yml` is the full
app-plus-PostgreSQL stack described in `DOCKER.md`.

SQLite is a lightweight fallback, but row-locking/concurrency tests require
PostgreSQL. Never point development at a production database or data root.

## Development accounts

An empty development database can receive disposable identities with:

```bash
cd backend
python manage.py seed_dev
```

This creates configured development identities, not microscopy data. The
login-page shortcuts appear only when mock login is enabled. Selecting one
fills the form and still requires an explicit Sign in.

## Tests

```bash
cd backend && python manage.py test
npm test --prefix frontend -- --run
npm run build --prefix frontend
./dev-setup.sh --check-git
```

Run Django tests from `backend/`; discovery is working-directory sensitive.
Use `npm run build:production --prefix frontend` only for the audited
`production_integrated_v1` host profile.
