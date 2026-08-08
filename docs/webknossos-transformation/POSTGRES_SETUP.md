# Local PostgreSQL setup

Development and concurrency testing run on PostgreSQL. SQLite remains available
as a lightweight fallback but **cannot** run the concurrency tests: it has no
row-level locking, `select_for_update()` is a documented no-op there, and the
Phase 0 baseline measured 87–95 % of concurrent task claims failing as a result
(`benchmarks/BASELINE.md` §5).

## Start it

```bash
cp .env.example .env          # first time only; then set MITO_DB_PASSWORD
docker compose -f docker-compose.dev.yml up -d
./dev-setup.sh                # installs the psycopg driver if missing
cd backend && python manage.py migrate
```

## The driver

PostgreSQL needs **`psycopg[binary]>=3.2`** — psycopg *3*, not psycopg2, which
is what Django 5 prefers and what every measurement in `BASELINE.md` was taken
against. It is declared in `environment.yml`, the project's single install path
(`pyproject.toml` deliberately carries no dependencies of its own).

`./dev-setup.sh` checks for it **only when `MITO_DB_ENGINE=postgres`**, so a
sqlite-only checkout is never told to install a driver it will not load. If it
is missing you get an explicit `core deps missing (psycopg)` with the install
command, rather than the failure mode this replaced: a `ModuleNotFoundError`
on the first `manage.py migrate`.

Installing by hand, if you are not using `dev-setup.sh`:

```bash
pip install 'psycopg[binary]>=3.2'
```

```bash
docker compose -f docker-compose.dev.yml down     # stop, keep data
docker compose -f docker-compose.dev.yml down -v  # stop and delete data
```

## Configuration

All via environment variables in `.env` (gitignored). Nothing is hard-coded.

| Variable | Default | Notes |
|---|---|---|
| `MITO_DB_ENGINE` | `sqlite` | `postgres` to use PostgreSQL. Defaults to sqlite so a checkout with no DB config behaves exactly as before. |
| `MITO_DB_NAME` | `mito_dev` | |
| `MITO_DB_USER` | `mito` | |
| `MITO_DB_PASSWORD` | — | **Generate your own.** Never reuse a deployment's. |
| `MITO_DB_HOST` | `127.0.0.1` | |
| `MITO_DB_PORT` | `5433` | Not 5432 — see below. |
| `MITO_DB_CONN_MAX_AGE` | `60` | Set `0` when running tests. |
| `MITO_SQLITE_NAME` | `backend/db.sqlite3` | Override to point at a throwaway copy. |

### Why port 5433

This host already runs an **unrelated WEBKNOSSOS stack** with its own
PostgreSQL (`webknossos-local-postgres-1`, compose project at `/opt/webknossos`).
Its database is container-internal and must not be touched. Using 5433, binding
to loopback, and pinning the compose project name (`mito-dev`) makes a stray
connection to the wrong database structurally difficult rather than merely
unlikely.

## Falling back to SQLite

```bash
MITO_DB_ENGINE=sqlite python manage.py test
```

Concurrency tests skip themselves automatically (`annotation/test_concurrency.py`
guards on `connection.vendor`) rather than asserting something the engine cannot
honour.

## Running tests

```bash
MITO_DB_CONN_MAX_AGE=0 python manage.py test --noinput
```

`--noinput` matters: a leftover `test_mito_dev` otherwise makes the runner
prompt for confirmation and die on `EOFError` in a non-interactive shell.

## What was migrated

The dev SQLite database held 5 users, 5 profiles, 4 annotator profiles and
1 token — no projects, volumes, or tasks. All of it was preserved:

1. `sqlite3.backup()` snapshot → `~/shenb/mito-backups/pre-postgres-<ts>/`
2. A **copy** was migrated forward and `dumpdata`-ed (the live SQLite was
   several migrations behind and could not be serialised in place; the original
   was never modified — verified by mtime and migration state)
3. `loaddata` into PostgreSQL after `migrate`

The original `backend/db.sqlite3` is untouched and still usable via
`MITO_DB_ENGINE=sqlite`.

> `loaddata` initially failed: `ensure_user_profile` fired on the User insert
> during the raw fixture load and collided with the fixture's own `UserProfile`
> row. Fixed by guarding on `raw` — see `accounts/signals.py` and
> `FixtureLoadingTests`. That bug broke **any** fixture containing users,
> including dump/restore of a whole database.
