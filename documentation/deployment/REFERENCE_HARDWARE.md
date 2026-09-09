# Development server and deployment profiles

## Reference development server snapshot

Observed on **2026-08-23**. This is the machine used for development and
verification; it is not a minimum requirement and not a performance result.

| Component | Recorded value |
| --- | --- |
| Operating system | Ubuntu 20.04.4 LTS, Linux 5.4.0-216-generic, x86-64 |
| CPU | Intel Core i9-7920X at 2.90 GHz; 12 physical cores, 24 logical CPUs, one socket |
| RAM | 125 GiB visible to the OS |
| Swap | 129 GiB |
| GPUs | 4 × NVIDIA GeForce RTX 2080 Ti |
| GPU memory | 11,264 MiB per GPU |
| Compute capability | 7.5 |
| GPU power limit | 250 W per GPU |
| NVIDIA driver | 570.207 |
| Driver-reported CUDA compatibility | CUDA 12.8 |
| Local storage | 1.9 TB Micron 1100 SATA SSD, ext4; 184 GB free at capture time |
| Docker | Engine 28.1.1; Compose v2.35.1 |
| Host tools | Git 2.25.1; Node 25.8.1; npm 11.11.0 |
| Project Python | Python 3.11.15 in the `mito-data-studio` conda environment |

GPU utilization and free disk/RAM are transient and must be captured again for
every benchmark. At snapshot time other processes occupied GPU memory, so the
snapshot must not be used as an idle-baseline measurement.

## Software environment distinction

The reproducible release environment is defined by `requirements-release.txt`,
`frontend/package-lock.json`, `environment.yml`, and the Docker build profiles.
The interactive conda environment on the reference host can differ: at capture
time it had ONNX Runtime 1.28.0 and did not expose h5py or nibabel, whereas the
release lock specifies ONNX Runtime GPU 1.26.0, h5py 3.16.0, and nibabel 5.3.2.
Manuscript experiments must state which environment was actually used.

## Hardware-adaptive recommendation on this host

The repository probe detects 24 logical CPUs, approximately 125 GiB RAM, four
11 GiB GPUs, and recommends a conservative starting point:

```dotenv
GUNICORN_WORKERS=2
GUNICORN_THREADS=2
MITO_SAM2_CUDA_DEVICE=0
MITO_AI_CUDA_DEVICE=1
MITO_TRACK_PLAN_MAX_VOXELS=256000000
MITO_SAM2_XY_PAD=256
MITO_SAM2_XY_MIN=512
MITO_SAM2_XY_MAX=1536
OMP_NUM_THREADS=16
MITO_DEPS=ai-gpu
```

This pins SAM 2 — which serves both Track and the prompted-mask tools — to
GPU 0. It does not automatically
use GPUs 2–3 for one Track batch.

## Supported deployment shapes

| Profile | Intended behavior |
| --- | --- |
| CPU-only, modest RAM | Core image and local Track provider, or AI-CPU for evaluation; smaller slab/crop limits |
| Single 12 GiB GPU | One or two gunicorn workers, SAM2 on device 0, approximately 1536px crop ceiling |
| Single 24 GiB GPU | Up to two workers and a larger crop/slab only after profiling |
| Two or more GPUs | SAM 2 uses one device; no automatic intra-batch Track sharding |

Run `ops/docker/detect-hardware.sh` on every deployment and archive its output
with experiment logs. Container limits and scheduler allocations can differ
from physical host capacity.

## Benchmark reporting minimum

Report CPU model/core allocation, RAM limit, GPU model/count/device assignment,
GPU memory, driver, CUDA/PyTorch/ONNX Runtime versions, source format, volume
shape/dtype/voxel size, Track class and branch counts, z ranges, xy crop
settings, slab cap, worker/thread counts, warm/cold model state, and timing-log
breakdown. Report peak RAM and GPU utilization using a stated sampling method.

