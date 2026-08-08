# Mito Data Agent — production image.
#
# One container serves everything: gunicorn runs the Django API and WhiteNoise
# serves the compiled SPA from the same process, so there is no nginx sidecar
# to configure. Put a TLS terminator in front of it in production (see
# DOCKER.md); the container itself speaks plain HTTP on $PORT.
#
# Build profiles — pick with --build-arg MITO_DEPS=<core|ai-cpu|ai-gpu>:
#
#   core     (default)  ~570 MB. No torch/ONNX. Full annotation, viewing,
#                       export, sharing. AI-assist tools report unavailable and
#                       tracking falls back to the 'local' provider.
#   ai-cpu              ~3 GB.  EfficientSAM + SAM2 on CPU. Slow but complete.
#   ai-gpu              ~8 GB.  CUDA 12.4 build. Needs an NVIDIA driver and the
#                       NVIDIA Container Toolkit on the host.
#
# Model weights are NOT baked in — vendor/ is ~1 GB of Git LFS and is mounted
# read-only at runtime instead, so `git lfs pull` is not a build prerequisite.


# --- Stage 1: compile the SPA ----------------------------------------------
FROM node:20-slim AS frontend

WORKDIR /build/frontend

# Dependencies first, so editing source does not re-resolve the lockfile.
# `npm ci` (not `install`) — the lockfile is the contract.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# `build` targets the `legacy` backend profile — the one with no extra runtime
# contract, and therefore the right default for a fresh deployment.
#
# Do not switch this to `build:production` on its own. That script builds for
# MITO_UPGRADE_PROFILE=production_integrated_v1, which backend/core/checks.py
# enforces as a hard contract: it refuses to start without a metrics bearer
# token, SAM2 + EfficientSAM weights of exact byte sizes, and specific CUDA
# device assignments. The SPA build and the backend profile must always agree —
# see the "Upgrade profiles" section of DOCKER.md.
ARG FRONTEND_BUILD_SCRIPT=build
RUN npm run "${FRONTEND_BUILD_SCRIPT}"


# --- Stage 2: python dependencies ------------------------------------------
# Built separately from the runtime so the (slow, large) dependency install is
# cached independently of application code, and so no compiler toolchain
# survives into the shipped image.
FROM python:3.11-slim AS pydeps

ARG MITO_DEPS=core

# build-essential covers the rare sdist that has no manylinux wheel; it stays
# in this stage only. libhdf5/libjpeg etc. are not needed — h5py, Pillow and
# scikit-image all ship self-contained wheels.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY ops/docker/requirements-core.txt ops/docker/requirements-ai-cpu.txt ops/docker/requirements-ai-gpu.txt /tmp/reqs/

# Installed into a venv so stage 3 can take the whole tree in one COPY.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
 && pip install -r "/tmp/reqs/requirements-${MITO_DEPS}.txt"


# --- Stage 3: runtime -------------------------------------------------------
FROM python:3.11-slim AS runtime

# libgomp1: OpenMP runtime that scikit-image/scipy/torch link against. Missing
# it produces an ImportError only on the first AI or watershed request, long
# after the container looks healthy — hence installed in every profile.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=pydeps /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings

# Unprivileged, with a fixed uid/gid so bind-mounted host directories have a
# predictable owner. Override at build time to match the host user that owns
# your data directory (see DOCKER.md § "File ownership").
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid "${APP_GID}" app \
 && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /bin/bash app

WORKDIR /app

COPY --chown=app:app backend/ ./backend/
COPY --chown=app:app --from=frontend /build/frontend/dist ./frontend/dist
COPY --chown=app:app ops/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Mount points, created up front so the container starts cleanly even when a
# deployment mounts nothing:
#   /data              MITO_DATA_ROOT — volumes, labels, submissions
#   /app/backend/media  Django MEDIA_ROOT — uploads
#   /state             sqlite database, when MITO_DB_ENGINE=sqlite
#   /vendor            model weights, read-only (ai-* profiles)
RUN mkdir -p /data /state /vendor /app/backend/media /app/backend/staticfiles \
 && chown -R app:app /data /state /app/backend/media /app/backend/staticfiles

# Baked-in defaults that point at those mount points. Every one is overridable
# from the env file; none encodes a host-specific path.
ENV MITO_DATA_ROOT=/data \
    MITO_SQLITE_NAME=/state/db.sqlite3 \
    MITO_SAM2_ROOT=/vendor/sam2 \
    MITO_SAM2_CHECKPOINT=/vendor/sam2/checkpoints/sam2.1_hiera_large.pt \
    MITO_CELLABLE_MODELS_ROOT=/vendor/efficient_sam \
    PORT=8000 \
    GUNICORN_WORKERS=3 \
    GUNICORN_THREADS=2

# --timeout 300 in the entrypoint is deliberate: slice reads touch multi-GB
# TIFFs and gunicorn's 30s default kills them mid-request.
USER app
EXPOSE 8000

# /healthz is process liveness only. /readyz additionally checks the database
# and free disk, which makes it the wrong probe here — a database that is still
# starting would restart an otherwise-healthy app container.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8000')}/healthz\", timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["serve"]
