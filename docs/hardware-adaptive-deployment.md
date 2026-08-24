# Hardware-adaptive development deployment

This is the portable deployment procedure for a fresh development checkout.
It is written for both human operators and coding agents. It does not use the
maintainer-specific paths or systemd services in `docs/deployment.md`.

## What hardware adaptation currently means

The probe detects host CPU count, RAM, visible NVIDIA GPU count, and GPU memory.
It recommends or applies:

- gunicorn worker and thread counts;
- the CUDA device used by SAM2 Track;
- a second CUDA device for EfficientSAM when at least two GPUs are visible;
- the maximum Track planning slab size;
- SAM2 crop padding and window bounds;
- CPU threads for merge/contact work;
- the Docker dependency profile used at image build time.

It does **not** split one Propagate-all request across every visible GPU. A
single worker owns mutable SAM2 state and processes queued classes in request
order so collision behavior remains deterministic. Do not increase worker
count aggressively on one GPU: each worker can load another model copy.

## Human operator procedure

### 1. Clone and create the development environment file

```bash
git clone https://github.com/BoyuShen2004/mito-data-studio.git
cd mito-data-studio
cp .env.docker.dev.example .env.docker.dev
```

Set at least these values in `.env.docker.dev`:

```dotenv
DJANGO_SECRET_KEY=<random-secret>
MITO_DB_PASSWORD=<database-password>
MITO_HOST_DATA_DIR=<host-directory-for-volume-data>
```

Do not commit `.env.docker.dev`.

### 2. Detect hardware and seed the configuration

Preview the recommendation, then apply only values that are not already set:

```bash
make docker-detect
ops/docker/detect-hardware.sh --apply .env.docker.dev
```

Review the resulting values. Explicit values already present in the env file
always win. Run the probe again after changing GPUs or RAM.

### 3. Choose CPU or GPU startup

CPU-only development stack:

```bash
make docker-dev-up
```

GPU development stack:

```bash
git lfs install
git lfs pull
docker compose -f docker-compose.dev-stack.yml --env-file .env.docker.dev \
  --profile gpu up -d --build app-gpu
```

The GPU path requires an NVIDIA driver and NVIDIA Container Toolkit. Verify the
host before building:

```bash
docker run --rm --gpus all \
  nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

### 4. Verify the deployment

```bash
docker compose -f docker-compose.dev-stack.yml --env-file .env.docker.dev ps
curl -fsS http://127.0.0.1:8000/healthz
docker compose -f docker-compose.dev-stack.yml --env-file .env.docker.dev logs --tail=100 app
```

For the GPU profile, inspect `app-gpu` instead of `app`:

```bash
docker compose -f docker-compose.dev-stack.yml --env-file .env.docker.dev \
  --profile gpu logs --tail=100 app-gpu
```

When testing Track, queue multiple ready classes and run Propagate all. The
`mito.track.timing` log lines report slab loading, per-class time, SAM2 time,
diff encoding, and total request time. Confirm or Reject the combined canvas
preview as one batch.

### 5. Stop the stack

```bash
make docker-dev-down
```

This preserves Docker volumes. Do not add `-v` unless database deletion is
intentional and independently confirmed.

## Instruction for an LLM coding agent

Give the agent this repository path and ask it to follow the contract below.
The agent should report exact commands, detected hardware, effective settings,
container status, and any unmet prerequisite.

```text
Work only in the development checkout of Mito Data Studio. Do not modify or
deploy the maintainer-specific production checkout unless explicitly asked.

Goal: prepare and validate the hardware-adaptive Docker development stack.

1. Read AGENTS.md, docs/product-invariants.md, this file, and docs/docker.md.
2. Inspect git status first. Preserve all existing user changes and secrets.
3. Check Docker Compose, Git LFS, NVIDIA Container Toolkit, visible GPUs, model
   files, host RAM, and available disk space. Use read-only checks first.
4. If .env.docker.dev is absent, copy .env.docker.dev.example. Never invent,
   print, or commit secrets. Stop and request the required secret values if
   they are unavailable.
5. Run ops/docker/detect-hardware.sh in report mode, then use --apply on
   .env.docker.dev. Never overwrite explicit operator values silently.
6. Use the ordinary app service on CPU-only hosts. Use the app-gpu profile only
   when NVIDIA container access works and Git LFS model weights are present.
7. Validate Compose configuration before starting it. Build and start only the
   selected profile; do not start CPU and GPU app services on the same port.
8. Verify container health, /healthz, and recent logs. On GPU, verify the logs
   show the intended CUDA device and do not silently claim multi-GPU sharding.
9. If Track test data and credentials are available, profile one realistic
   Propagate-all batch and record mito.track.timing output. Do not create,
   overwrite, or delete microscopy data merely to manufacture a benchmark.
10. Never use docker compose down -v, delete data roots, edit production env
    files, or deploy/reload production services without explicit permission.
11. Finish with a concise report: hardware found, settings applied, services
    started, health result, tests performed, limitations, and exact next step.

Current architecture boundary: one SAM2 worker serializes parents to preserve
mutable model state and request-order collision semantics. Hardware detection
selects safe process/memory/crop settings, but it does not distribute one Track
batch across all GPUs.
```

## Common outcomes

| Host | Expected behavior |
| --- | --- |
| CPU-only laptop/server | Core or AI-CPU image; Track can use the local provider, with a smaller slab/crop recommendation |
| One NVIDIA GPU | SAM2 uses device 0; worker count and crop maximum are limited according to VRAM |
| Two or more NVIDIA GPUs | SAM2 defaults to device 0 and EfficientSAM to device 1; remaining GPUs are not automatically used for one Track batch |
| Explicit env overrides | The probe reports recommendations but does not replace the operator's values |

For all environment variables and troubleshooting, continue with
[Docker deployment](docker.md#hardware-auto-tuning).
