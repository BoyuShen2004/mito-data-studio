# Deploying with Docker

A container deployment of Mito Data Studio, meant for someone who has just
cloned the repository and wants a running instance without reproducing the
conda/CUDA development environment.

One container runs everything: gunicorn serves the Django API, and WhiteNoise
serves the compiled single-page frontend from the same process. There is no
nginx sidecar to configure. A second container runs PostgreSQL.

> This is a different thing from `docker-compose.dev.yml`, which only starts a
> database for development against a host checkout. The two never run together.
>
> It is also different from the systemd/gunicorn deployment described in
> [`DEPLOYMENT.md`](DEPLOYMENT.md), which documents the maintainer's own
> production host. Follow that file for that machine; follow this one for a
> fresh deployment anywhere else.

---

## Prerequisites

- **Docker Engine 24+** with the Compose plugin (`docker compose version`).
- **~2 GB of disk** for the default image, plus whatever your volume data needs.
- **Nothing else.** Python, Node, conda and CUDA are all handled inside the
  build; you do not need them on the host.

Optional, and only for the AI-assisted tools:

- **Git LFS**, to fetch the model weights under `vendor/` (~1 GB):
  ```bash
  git lfs install && git lfs pull
  ```
  The weights are deliberately *not* baked into the image — they are mounted
  read-only at runtime — so `git lfs pull` is not a build prerequisite and a
  deployment that never uses AI assist can skip it entirely.
- **An NVIDIA driver + the NVIDIA Container Toolkit**, for the GPU profile.

---

## Quick start

```bash
git clone https://github.com/BoyuShen2004/mito-data-studio.git
cd mito-data-studio

cp .env.docker.example .env.docker
# Now edit .env.docker — see "Required settings" below.

docker compose --env-file .env.docker up -d --build
```

Open <http://localhost:8000>.

First build takes a few minutes (npm install + the Python wheels). Later builds
reuse the cached dependency layers and take seconds unless you change
`package-lock.json` or the requirements files.

### `--env-file .env.docker` is required

Pass it on **every** `docker compose` command. Compose resolves the `${...}`
references in `docker-compose.yml` from its own env file, which defaults to
`.env` — and `.env` in this repository is the *host development* configuration,
pointing at a different database on a different port. Without the flag, compose
would quietly build your deployment out of those values.

To stop repeating it, export it once per shell:

```bash
export COMPOSE_ENV_FILES=.env.docker
docker compose up -d --build      # flag no longer needed in this shell
```

Every command in the rest of this document assumes you have done one or the
other.

### Required settings

Four entries in `.env.docker` need your attention before the first start; the
file explains the rest inline.

| Variable | What to set it to |
| --- | --- |
| `DJANGO_SECRET_KEY` | A fresh random string. `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `MITO_DB_PASSWORD` | A fresh random string. Chosen **before** the first start — see the note below. |
| `DJANGO_ALLOWED_HOSTS` | Every hostname the deployment answers to. Django rejects requests with any other `Host` header. |
| `MITO_HOST_DATA_DIR` | Host directory holding your volume data. Bind-mounted to `/data`. |

> **Pick the database password before the first `up`.** PostgreSQL creates the
> role only when it initialises an empty data directory. Changing
> `MITO_DB_PASSWORD` later leaves the container authenticating with the new
> value against a role that still has the old one, and you get
> `password authentication failed for user "mito"`. Fix it with an
> `ALTER ROLE`, or — only if the database holds nothing you need —
> `docker compose down -v`, which **deletes all data**.

Also set `APP_UID`/`APP_GID` to the owner of `MITO_HOST_DATA_DIR`; see
[File ownership](#file-ownership).

### Creating the first account

Uncomment the `DJANGO_SUPERUSER_*` block in `.env.docker` before the first
start and the entrypoint creates that account. Or do it any time afterwards:

```bash
docker compose exec app /usr/local/bin/entrypoint.sh manage createsuperuser
```

The account is never overwritten on later starts, so editing the password in
`.env.docker` afterwards does nothing. Change it with
`... manage changepassword <username>`, and clear the variables once the
account exists.

---

## Build profiles

The AI-assist stack (PyTorch + ONNX Runtime) is roughly ten times the size of
everything else, so it is opt-in. Every `import torch` and `import onnxruntime`
in the codebase is lazy, and `annotation/tracking/registry.py` falls back to the
`local` tracking provider with a logged warning when torch is missing — so the
default image starts, serves and annotates normally. Only the
AI-assisted tools degrade.

Set `MITO_DEPS` in `.env.docker`:

| Profile | Size | What you get |
| --- | --- | --- |
| `core` *(default)* | ~570 MB | Everything except AI assist. Annotation, viewing, 3-D meshes, watershed split, review and sharing. |
| `ai-cpu` | ~3 GB | Adds EfficientSAM Point/Box Mask and SAM2 tracking on CPU. Complete, but slow enough that it is best kept for trying the tools out. |
| `ai-gpu` | ~8 GB | The same on CUDA 12.4. Needs a GPU host — see below. |

Both AI profiles also need the `vendor/` weights fetched with `git lfs pull`
and `MITO_HOST_VENDOR_DIR` pointing at them.

### GPU

Requires an NVIDIA driver and the [NVIDIA Container
Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
on the host. Verify with:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

Then use the `gpu` compose profile, which runs the `app-gpu` service instead of
`app`:

```bash
docker compose --env-file .env.docker --profile gpu up -d --build app-gpu
```

It is a separate service rather than a flag on `app` because a GPU reservation
makes compose fail outright on hosts without the toolkit, which would break the
CPU path for everyone.

With more than one worker, note that each gunicorn worker loads its own copy of
the model — three workers means three times the VRAM. Drop `GUNICORN_WORKERS`
to 1–2 on a single GPU.

---

## Upgrade profiles

`MITO_UPGRADE_PROFILE` selects a feature contract, and the SPA must be compiled
for the same one — `FRONTEND_BUILD_SCRIPT` picks the npm script that does it.
The backend cross-checks the pairing at startup and refuses to run on a
mismatch.

| `MITO_UPGRADE_PROFILE` | `FRONTEND_BUILD_SCRIPT` | Notes |
| --- | --- | --- |
| `legacy` *(default)* | `build` | No extra runtime contract. Use this. |
| `legacy` | `build:no-demo` | Same, with the demo-account UI hidden. |
| `production_integrated_v1` | `build:production` | Advanced — read below. |

`production_integrated_v1` is **not** a "more production" setting. It is an
audited contract for one specific two-GPU host, and `backend/core/checks.py`
refuses to start unless every clause holds: PostgreSQL, a non-empty
`MITO_METRICS_BEARER_TOKEN`, an empty `MITO_PROCESSING_ENV_ALLOWLIST`, SAM2
pinned to CUDA device 0 with EfficientSAM on device 1, and SAM2 +
EfficientSAM weight files matching exact byte sizes. Selecting it without all
of that produces `deployment.E029`/`E030` and a container that restarts
forever.

---

## Ports and volumes

### Ports

The app publishes **`127.0.0.1:8000`** — loopback only, deliberately. Put a
TLS-terminating reverse proxy in front of it for anything reachable from
outside the host, and uncomment the proxy-related variables in `.env.docker`
(`DJANGO_CSRF_TRUSTED_ORIGINS`, `MITO_TRUST_PROXY_SSL_HEADER`, the
`*_COOKIE_SECURE` pair) so Django knows requests arrive over HTTPS.

Change the host port with `MITO_HOST_PORT`. Binding to all interfaces means
editing the `ports:` line in `docker-compose.yml`, and is only appropriate on a
trusted private network.

PostgreSQL is **not** published to the host at all; the app reaches it over the
compose network. Uncomment its `ports:` block if you need a client.

### Volumes

| Mount | Kind | Holds | Back up? |
| --- | --- | --- | --- |
| `${MITO_HOST_DATA_DIR}` → `/data` | bind | `MITO_DATA_ROOT`: image volumes, labels, submissions, per-volume artifacts | **Yes — this is your data** |
| `mito-pgdata` | named | The PostgreSQL database: users, projects, tasks | **Yes** |
| `mito-media` | named | Django `MEDIA_ROOT` uploads | Yes |
| `mito-state` | named | sqlite database, if you switch `MITO_DB_ENGINE` | Yes, if used |
| `${MITO_HOST_VENDOR_DIR}` → `/vendor` | bind, read-only | Model weights | No — refetch with `git lfs pull` |

The data root is a bind mount rather than a named volume on purpose: it is the
directory you fill with data, inspect and back up, so it should be somewhere
you chose on a disk with room to grow.

### File ownership

The container runs as an unprivileged user whose uid/gid you set with
`APP_UID`/`APP_GID` (default `1000`). If those do not match the owner of your
host data directory, the app cannot write to `/data` and refuses to start with:

```
deployment.E006  MITO_DATA_ROOT is not writable by this process: /data
```

Fix it by setting them to your own ids and rebuilding:

```bash
id -u   # -> APP_UID
id -g   # -> APP_GID
docker compose --env-file .env.docker up -d --build
```

They are build arguments, not runtime settings, so this needs a rebuild — a
fast one, since only the final stage is invalidated.

---

## Operating

All commands assume `--env-file .env.docker` or the exported `COMPOSE_ENV_FILES`.

### Status, logs, health

```bash
docker compose ps
docker compose logs -f app
docker compose logs postgres

curl localhost:8000/healthz   # process liveness
curl localhost:8000/readyz    # database + data root + free disk
```

`/healthz` is what the container healthcheck uses. `/readyz` additionally
checks the database and free disk, which makes it the wrong probe for
restarting the container — a database still starting up would kill an otherwise
healthy app.

### Stop, start, restart

```bash
docker compose stop            # keeps containers and all data
docker compose start
docker compose restart app

docker compose down            # removes containers, KEEPS named volumes
docker compose down -v         # ALSO DELETES the database and media. See below.
```

> **`down -v` deletes your database.** It removes `mito-pgdata`, `mito-media`
> and `mito-state` — every user, project, task and submission. Your image data
> under `MITO_HOST_DATA_DIR` survives, because it is a bind mount. Reach for
> `down` without `-v` unless you specifically mean to start over.

### Updating

```bash
git pull
docker compose --env-file .env.docker up -d --build
```

The entrypoint applies migrations and collects static files on every start, so
there is no separate migrate step. Changing `.env.docker` needs
`docker compose up -d` (recreates the container), not `restart` — the container
reads its environment only at creation.

### Running management commands

```bash
docker compose exec app /usr/local/bin/entrypoint.sh manage <command>

# e.g.
docker compose exec app /usr/local/bin/entrypoint.sh manage createsuperuser
docker compose exec app /usr/local/bin/entrypoint.sh manage changepassword alice
docker compose exec app /usr/local/bin/entrypoint.sh manage check
```

The `manage` wrapper handles the working directory and waits for the database.
A bare `docker compose exec app python manage.py ...` fails with
`ModuleNotFoundError: No module named 'config'`, because the container's
working directory is `/app`, not `/app/backend`.

### Database backup and restore

```bash
# Backup
docker compose exec -T postgres pg_dump -U mito mito | gzip > mito-$(date +%F).sql.gz

# Restore into an empty database
gunzip -c mito-2026-08-05.sql.gz | docker compose exec -T postgres psql -U mito mito
```

Back up `MITO_HOST_DATA_DIR` separately — the dump contains metadata and paths,
not the volume data itself.

---

## Troubleshooting

**`deployment.E006 MITO_DATA_ROOT is not writable`** — uid mismatch on the
bind mount. See [File ownership](#file-ownership).

**`password authentication failed for user "mito"`** — `MITO_DB_PASSWORD` was
changed after the database was initialised. See the note under
[Required settings](#required-settings).

**`deployment.E029` / `E030`** — `MITO_UPGRADE_PROFILE=production_integrated_v1`
without its full contract. Set it back to `legacy` with
`FRONTEND_BUILD_SCRIPT=build`. See [Upgrade profiles](#upgrade-profiles).

**`deployment.E023`/`E024`/`E025`** — feature flags that disagree with what the
SPA was built with. `build` compiles neither chunk transport nor renderer, so
`VITE_FEATURE_CHUNK_PULL_QUEUE` and `VITE_FEATURE_CHUNK_RENDERER` must stay
off unless you build for a profile that includes them.

**`DisallowedHost` in the logs** — add the hostname to
`DJANGO_ALLOWED_HOSTS`, then `docker compose up -d`.

**AI tools report unavailable** — expected on the `core` image. Switch
`MITO_DEPS` to `ai-cpu` or `ai-gpu`, run `git lfs pull`, and rebuild.

**Compose picked up the wrong settings** — you almost certainly omitted
`--env-file .env.docker`. Check what it actually resolved with
`docker compose --env-file .env.docker config`.
