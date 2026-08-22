# Development

This guide is for contributors running Django and Vite from the source tree.
For a complete container deployment, use [Docker deployment](docker.md). Never point a
development checkout at the production database or production data root.

## First setup

Requirements: Git LFS, conda or mamba, Node (provided by the conda environment),
and about 10 GB of free space. A compatible NVIDIA GPU is optional.

```bash
git clone https://github.com/BoyuShen2004/mito-data-studio.git
cd mito-data-studio
git lfs install
git lfs pull
conda env create -f environment.yml
conda activate mito-data-studio
docker compose -f docker-compose.dev.yml up -d
make setup
make dev
```

Open <http://localhost:5173>. Django runs on `127.0.0.1:8000`; Vite proxies API
requests to it. `docker-compose.dev.yml` starts only PostgreSQL on port 5433.
It is not the full application stack in `docker-compose.yml`.

`scripts/dev/setup.sh` creates `.env` only when it is missing, checks the environment,
installs frontend packages when needed, runs Django checks, and applies additive
migrations. It does not modify an existing `.env` or reshape a conda environment.

Useful variants:

```bash
scripts/dev/setup.sh --install-deps  # install missing lightweight Python packages
scripts/dev/setup.sh --smoke         # load AI runtimes and build the frontend
make check-git                       # reject staged runtime data, DBs, and secrets
```

## Daily work

```bash
conda activate mito-data-studio
docker compose -f docker-compose.dev.yml up -d
make dev
```

React, CSS, and Django changes reload automatically. Run `make setup` after
pulling migrations or dependency-lock changes. To run the processes separately:

```bash
python manage.py runserver
npm run dev --prefix frontend
```

The pyramid dispatcher is a separate process when queued pyramid jobs need to
be consumed:

```bash
python manage.py run_processing_dispatcher
```

## Development accounts and reset

Create the standard disposable accounts with:

```bash
python manage.py seed_dev
python manage.py dev_status
```

The default password is `demo12345`. The configured manager, annotator, and
requester entries appear on the development login page only when mock login is
enabled. Selecting one fills the form; it never signs in automatically.

Reset commands are destructive to the configured development database and
`MITO_DATA_ROOT`:

```bash
python manage.py clear_dev_data
python manage.py clear_dev_data --keep-users
python manage.py reset_dev
```

They preserve superusers and refuse to run with `DEBUG=False` unless explicitly
forced. Keep `MITO_DATA_ROOT` outside the repository when possible. Never use a
production path for development.

## Configuration

The root `.env` is copied from `.env.example`. Important settings include:

- `MITO_DATA_ROOT`: image, mask, working-label, submission, and pyramid storage.
- `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, and `DJANGO_ALLOWED_HOSTS`.
- `DJANGO_CORS_ORIGINS`: browser origins allowed to call Django.
- AI/tracking provider and model settings documented in `.env.example`.

Source images and region masks are immutable inputs. Annotation writes go to an
application-owned working mask; approval creates the official checkpoint.

## Tests

```bash
python manage.py test
npm test --prefix frontend -- --run
npm run typecheck --prefix frontend
npm run build --prefix frontend
make check-git
```

Run Django tests from `backend/`; its discovery is working-directory sensitive.
Use the ordinary frontend build for development. `build:production` is reserved
for the audited production profile.

For targeted work, both suites accept a test path or name. Run migrations and
both builds before promoting development source into the production checkout.

## Data and Git safety

Never commit `.env`, SQLite databases, `data/`, volume binaries, generated
pyramids, logs, caches, `node_modules`, `frontend/dist`, or a virtualenv. Check
before every push:

```bash
make check-git
git status --short
```

Production promotion is development → production. Production-only fixes should
first be ported back and tested here. The host-specific procedure is in
[host deployment guide](deployment.md).
