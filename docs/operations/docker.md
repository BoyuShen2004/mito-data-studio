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
> [`deployment.md`](production-host.md), which documents the maintainer's own
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

cp config/env/docker.env.example .env.docker
ops/docker/detect-hardware.sh --apply .env.docker
# Now edit secrets/hosts in .env.docker — see "Required settings" below.

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

The AI-assist stack (PyTorch) is roughly ten times the size of everything
else, so it is opt-in. Every `import torch` in the codebase is lazy, and `annotation/tracking/registry.py` falls back to the
`local` tracking provider with a logged warning when torch is missing — so the
default image starts, serves and annotates normally. Prompted SAM2 tools report unavailable. If `sam2` is selected and torch is
missing, Track falls back to the simple local intensity-based stand-in; this is
not SAM2 CPU inference. When torch exists but CUDA does not, the SAM2 provider
raises instead of falling back. Ordinary annotation remains usable.

Set `MITO_DEPS` in `.env.docker`:

| Profile | Size | What you get |
| --- | --- | --- |
| `core` *(default)* | ~570 MB | Annotation, viewing, 3-D meshes, watershed split, review and sharing; excludes AI and kimimaro measurements. |
| `ai-cpu` | ~3 GB | Installs CPU PyTorch. The application SAM2 provider requires CUDA, so this profile does not enable SAM2 masks or SAM2 Track. Explicit local tracking remains a non-model stand-in. |
| `ai-gpu` | ~8 GB | Adds SAM2 masks and Track on CUDA 12.4. Needs a GPU host and weights; excludes kimimaro measurements. |

SAM2 requires the `ai-gpu` profile, usable CUDA and the `vendor/` weights fetched
with `git lfs pull`, with `MITO_HOST_VENDOR_DIR` pointing at them. Installing
CPU torch and weights does not bypass the CUDA requirement.

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
SAM 2 (used by both Track and the prompted-mask tools) — two workers need twice
the VRAM of one. Keep `GUNICORN_WORKERS` at 1–2 on a single GPU.

---

## Hardware auto-tuning

Track batch propagation and SAM2 are sensitive to CPU cores, system RAM, GPU
count, and VRAM. Rather than hard-coding one host's numbers, use the probe
script and env-driven settings.

### Probe script

```bash
ops/docker/detect-hardware.sh                    # human-readable report
ops/docker/detect-hardware.sh --apply .env.docker
ops/docker/detect-hardware.sh --apply .env.docker.dev
```

It suggests (without overwriting existing values):

| Variable | Purpose |
| --- | --- |
| `GUNICORN_WORKERS` / `GUNICORN_THREADS` | Web concurrency; keep workers low on single-GPU hosts |
| `MITO_SAM2_CUDA_DEVICE` | GPU index for SAM2 Track |
| `MITO_TRACK_PLAN_MAX_VOXELS` | Max slab size for Propagate all |
| `MITO_SAM2_XY_PAD` / `MITO_SAM2_XY_MIN` / `MITO_SAM2_XY_MAX` | SAM2 crop padding and window bounds (VRAM vs speed tradeoff) |
| `OMP_NUM_THREADS` | CPU threads for merge/contact steps between GPU passes |
| `MITO_DEPS` | Build profile hint (`core` / `ai-cpu` / `ai-gpu`) |

### Runtime auto-tune

Set `MITO_HARDWARE_AUTO_TUNE=1` in `.env.docker` or `.env.docker.dev`. On each
container start the entrypoint runs the probe and exports any **unset** sizing
variables. Explicit values in the env file always win.

Development stack defaults enable this and deliberately leave probe-managed
settings commented out (`config/env/docker-dev.env.example`). Production deployments
should run `--apply` once, review the output, then set
MITO_HARDWARE_AUTO_TUNE=0 when values are stable.

### Track batch execution and profiling

Propagate all stays on the read-only plan endpoint: it copies only the combined
z slab, reads labels as one contiguous slab (including pending browser planes),
then applies classes in request order so the first class still wins collisions.
The SAM2 adapter owns mutable inference state and a process-local lock, so a
single worker intentionally runs parents serially. Do not raise gunicorn worker
count as a substitute for intra-batch parallelism on one GPU: every process
loads another model copy and can exhaust VRAM. Multi-GPU parent sharding needs a
dedicated process-isolated worker and ordered CPU merge; it is not enabled by
the current synchronous API.

Use `docker compose logs -f app-gpu` while profiling. The
`mito.track.timing` logger records image copy, contiguous label load, each
class's position and wall time, diff encoding, SAM2 initialization/propagation,
and total plan time. The browser shows the queued class count and a live elapsed
timer for the entire request; Confirm/Reject remains a single batch review.

### Deployment profiles (manual reference)

| Host shape | Starting point |
| --- | --- |
| Laptop, 16 GB RAM, no GPU | `MITO_DEPS=core`, workers 3–4, skip Track GPU |
| Workstation, 1× 12 GB GPU | `MITO_DEPS=ai-gpu`, `GUNICORN_WORKERS=1`, `MITO_SAM2_XY_MAX=1536` |
| Server, 1× 24 GB GPU | `GUNICORN_WORKERS=2`, `MITO_TRACK_PLAN_MAX_VOXELS=256000000` |
| Server, 2× GPU (24 GB each) | `MITO_SAM2_CUDA_DEVICE=0`, workers 2 |
| Large RAM (64 GB+), big EM planes | Raise `MITO_TRACK_PLAN_MAX_VOXELS` and `MITO_SAM2_XY_MAX` after profiling |

Re-run the probe after hardware changes.

## Upgrade profiles

`MITO_UPGRADE_PROFILE` selects the backend feature defaults/deployment identity;
`FRONTEND_BUILD_SCRIPT` selects browser flags. Startup checks validate declared
`VITE_*` environment values against backend dependencies. They do not inspect
the compiled SPA; operators must ensure declarations describe the actual build.

For a portable real-data deployment, keep backend identity `legacy`, choose
`FRONTEND_BUILD_SCRIPT=build:no-demo`, enable the nine backend features below and
declare both browser chunk flags. This uses the same functional feature set as
development/production while disabling the demo-account UI, without claiming the
host-specific audited backend identity. Add these values to `.env.docker`:

```dotenv
FRONTEND_BUILD_SCRIPT=build:no-demo
MITO_UPGRADE_PROFILE=legacy
FEATURE_TEAMS=true
FEATURE_AUTO_FILL_SCHEDULER=true
FEATURE_REVIEW_HISTORY=true
FEATURE_DASHBOARDS=true
FEATURE_ANNOTATION_OPS=true
FEATURE_INTERPOLATION=true
FEATURE_ANNOTATION_TOOLS=true
FEATURE_VOLUME_PYRAMIDS=true
FEATURE_CHUNK_SERVICE=true
VITE_FEATURE_CHUNK_PULL_QUEUE=true
VITE_FEATURE_CHUNK_RENDERER=true
ENABLE_MOCK_DEV_LOGIN=false
MITO_ALLOW_DEV_RESET=false
```

The checked-in Docker templates currently enable only the two streaming backend
flags. Follow [Feature flags](../development.md#feature-flags--development-runs-what-production-runs)
for the full application contract; selecting `legacy` alone does not enable it.

| `MITO_UPGRADE_PROFILE` | `FRONTEND_BUILD_SCRIPT` | Notes |
| --- | --- | --- |
| `legacy` *(template default)* | `build` | Development-account build; chunk frontend defaults off. Enable all nine backend features for full workflows. |
| `legacy` | `build:no-demo` | Integrated chunk frontend, demo UI hidden; use the aligned settings above. |
| `production_integrated_v1` | `build:production` | Advanced — read below. |

`production_integrated_v1` is **not** a "more production" setting. It is an
audited deployment contract, and `backend/core/checks.py`
refuses to start unless every clause holds: PostgreSQL, a non-empty
`MITO_METRICS_BEARER_TOKEN`, an empty `MITO_PROCESSING_ENV_ALLOWLIST`, SAM2
pinned to CUDA device 0, and the SAM2 checkpoint matching its exact byte
size. Selecting it without all
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
The root `manage.py` adds `backend/` to the import path, and the Dockerfile
also sets `PYTHONPATH=/app/backend`, so a bare `python manage.py` works too;
the wrapper additionally waits for PostgreSQL.

### Queued processing

Neither Compose app service starts a dispatcher; the entrypoint runs only
gunicorn for `serve`. Start queued pyramid processing separately in another
terminal, using the same environment/storage as the web app:

```bash
docker compose --env-file .env.docker exec app \
  /usr/local/bin/entrypoint.sh manage run_processing_dispatcher --job-type build_pyramid
```

Use `app-gpu` in place of `app` when that is the selected service. `exec` ends when
the process/container ends; supervise a dispatcher for ongoing operation. This
command is appropriate for the included pyramid runtime. Measurement dispatch
also requires kimimaro and its dependencies, which all current Docker profiles
omit. See [measurement prerequisites](../engineering/measurements.md#deployment-prerequisites)
before adding `--job-type measure_mito`; merely queueing a run is insufficient.
Generic external pipelines require caller-supplied commands and the configured
[processing backend](../engineering/architecture.md#background-processing).

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
`MITO_DEPS` to `ai-gpu`, verify NVIDIA container CUDA access, run `git lfs pull`,
and rebuild. The ai-cpu profile cannot run the application SAM2 provider.

**Compose picked up the wrong settings** — you almost certainly omitted
`--env-file .env.docker`. Check what it actually resolved with
`docker compose --env-file .env.docker config`.
