# Development checklist

The short version of [Conda development](conda-dev.md): what to run, in order,
to get a working checkout and prove it still works. Two paths are supported and
they are not interchangeable — pick one per session.

| Path | You edit code with | Database | Use when |
| --- | --- | --- | --- |
| **Host Django + Vite** | live reload on the host | `docker-compose.dev.yml` PostgreSQL on `127.0.0.1:5433` | day-to-day development |
| **Full Docker** | rebuild per change | PostgreSQL inside the stack | reproducing a deployment, or a clone-to-running smoke |

## Path A — host Django + Vite (day-to-day)

```bash
# 1. Start the development database only (never the full stack).
docker compose -f docker-compose.dev.yml up -d

# 2. Apply migrations. Additive only; never reset as a shortcut.
cd backend && python manage.py migrate && cd ..

# 3. Optional: disposable development identities on an empty database.
cd backend && python manage.py seed_dev && cd ..

# 4. Run backend and frontend together (Ctrl+C stops both).
./dev-launch.sh
```

Open <http://localhost:5173>. Vite proxies the API to Django on `:8000`.

`docker-compose.dev.yml` starts **only** PostgreSQL. It is pinned to project
name `mito-dev` and port `5433` so it can never adopt or collide with the
unrelated WEBKNOSSOS stack on this host.

## Path B — full Docker

```bash
cp .env.docker.example .env.docker      # then fill in the REQUIRED section
docker compose --env-file .env.docker up -d --build
```

Open <http://localhost:8000>. Details, build profiles, GPU, persistence and
backup live in [Docker deployment](../../DOCKER.md).

To validate compose without a real `.env.docker`, point the container env file
at the checked-in template instead of creating one:

```bash
MITO_CONTAINER_ENV_FILE=.env.docker.example \
  docker compose --env-file .env.docker.example config --quiet
```

## Tests

```bash
cd backend && python manage.py test        # run from backend/; discovery is cwd-sensitive
npm test --prefix frontend -- --run
npm run typecheck --prefix frontend
npm run build --prefix frontend
./dev-setup.sh --check-git                 # rejects tracked secrets, DBs, volume data
```

`npm run build:production` is the audited `production_integrated_v1` host
profile. Use plain `build` for development verification.

## How this differs from production

Production on this host is **not** any of the above: it runs under systemd as a
separate service user, from its own checkout, with its own virtualenv, database
and data root. See [This-host deployment](../../DEPLOYMENT.md).

| | Development | Production |
| --- | --- | --- |
| Process | `dev-launch.sh` or Docker Compose | `mito-data-agent-v1.1.1.service` (+ dispatcher) |
| Server | Vite `:5173` + Django `:8000` | gunicorn on `127.0.0.1:18191` |
| Database | `mito_dev` on `:5433` | production PostgreSQL |
| Data root | local `./data` | the production `MITO_DATA_ROOT` |
| Static files | served by Vite | built bundle + WhiteNoise |

Development must never point at the production database or data root. After a
change is pushed, production is updated by promoting **development → production**
per [DEPLOYMENT.md](../../DEPLOYMENT.md), not the other way around.
