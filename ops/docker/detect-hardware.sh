#!/usr/bin/env bash
# Probe CPU, RAM, and (when present) NVIDIA GPUs, then recommend runtime settings
# for gunicorn and Track/SAM2 batch work.
#
# Usage:
#   ops/docker/detect-hardware.sh              # print a human-readable report
#   ops/docker/detect-hardware.sh --export     # print shell export statements
#   ops/docker/detect-hardware.sh --apply FILE # merge recommendations into FILE
#
# The entrypoint sources this with --export when MITO_HARDWARE_AUTO_TUNE=1.
# Values already set in the environment are never overwritten.
set -euo pipefail

mode="${1:-report}"
target_file="${2:-}"

cores="$(nproc 2>/dev/null || echo 4)"
mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 16777216)"
mem_gb="$(( mem_kb / 1024 / 1024 ))"

gpu_count=0
gpu_mem_mb=0
if command -v nvidia-smi >/dev/null 2>&1; then
  mapfile -t gpu_lines < <(nvidia-smi -L 2>/dev/null || true)
  gpu_count="${#gpu_lines[@]}"
  if [ "$gpu_count" -gt 0 ]; then
    mapfile -t gpu_mem_lines < <(
      nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null || true
    )
    gpu_mem_mb="${gpu_mem_lines[0]// /}"
    gpu_mem_mb="${gpu_mem_mb:-0}"
  fi
fi

# Each gunicorn worker with SAM2 loads its own model (~2.4 GB weights + runtime).
if [ "$gpu_count" -ge 1 ]; then
  if [ "$gpu_mem_mb" -lt 10000 ]; then
    rec_workers=1
  elif [ "$gpu_mem_mb" -lt 20000 ]; then
    rec_workers=2
  else
    rec_workers=2
  fi
  rec_sam2_device=0
  rec_deps="ai-gpu"
else
  rec_workers=$(( cores * 2 + 1 ))
  if [ "$rec_workers" -gt 8 ]; then
    rec_workers=8
  fi
  rec_sam2_device=0
  if [ "$mem_gb" -ge 16 ]; then
    rec_deps="ai-cpu"
  else
    rec_deps="core"
  fi
fi

if [ "$cores" -le 4 ]; then
  rec_threads=1
else
  rec_threads=2
fi

if [ "$mem_gb" -ge 32 ]; then
  rec_track_voxels=256000000
elif [ "$mem_gb" -ge 16 ]; then
  rec_track_voxels=128000000
else
  rec_track_voxels=64000000
fi

if [ "$gpu_count" -ge 1 ] && [ "$gpu_mem_mb" -ge 24000 ]; then
  rec_xy_max=2048
elif [ "$gpu_count" -ge 1 ]; then
  rec_xy_max=1536
else
  rec_xy_max=1024
fi
rec_xy_pad=256
rec_xy_min=512

rec_omp="$cores"
if [ "$rec_omp" -gt 16 ]; then
  rec_omp=16
fi

declare -A REC=(
  [GUNICORN_WORKERS]="$rec_workers"
  [GUNICORN_THREADS]="$rec_threads"
  [MITO_SAM2_CUDA_DEVICE]="$rec_sam2_device"
  [MITO_TRACK_PLAN_MAX_VOXELS]="$rec_track_voxels"
  [MITO_SAM2_XY_PAD]="$rec_xy_pad"
  [MITO_SAM2_XY_MIN]="$rec_xy_min"
  [MITO_SAM2_XY_MAX]="$rec_xy_max"
  [OMP_NUM_THREADS]="$rec_omp"
  [MITO_DEPS]="$rec_deps"
)

case "$mode" in
  --export)
    for key in "${!REC[@]}"; do
      # Dependency profile is a Docker build argument. Changing it inside an
      # already-built container cannot install wheels retroactively; --apply
      # still writes the recommendation before `docker compose build`.
      [ "$key" = "MITO_DEPS" ] && continue
      if [ -z "${!key:-}" ]; then
        printf 'export %s=%q\n' "$key" "${REC[$key]}"
      fi
    done
    ;;
  --apply)
    if [ -z "$target_file" ]; then
      echo "usage: $0 --apply <env-file>" >&2
      exit 1
    fi
    touch "$target_file"
    for key in GUNICORN_WORKERS GUNICORN_THREADS MITO_SAM2_CUDA_DEVICE \
      MITO_TRACK_PLAN_MAX_VOXELS MITO_SAM2_XY_PAD MITO_SAM2_XY_MIN \
      MITO_SAM2_XY_MAX OMP_NUM_THREADS MITO_DEPS MITO_HARDWARE_AUTO_TUNE; do
      value="${REC[$key]:-}"
      [ -n "$value" ] || continue
      if grep -q "^${key}=" "$target_file" 2>/dev/null; then
        continue
      fi
      printf '%s=%s\n' "$key" "$value" >>"$target_file"
    done
    if ! grep -q '^MITO_HARDWARE_AUTO_TUNE=' "$target_file" 2>/dev/null; then
      printf '%s=%s\n' MITO_HARDWARE_AUTO_TUNE 1 >>"$target_file"
    fi
    ;;
  *)
    cat <<EOF
Hardware probe
  CPU cores:     ${cores}
  RAM:           ${mem_gb} GiB
  NVIDIA GPUs:   ${gpu_count}$([ "$gpu_count" -gt 0 ] && echo " (${gpu_mem_mb} MiB on device 0)" || true)

Recommended settings (only applied when MITO_HARDWARE_AUTO_TUNE=1 and unset)
  GUNICORN_WORKERS=${rec_workers}
  GUNICORN_THREADS=${rec_threads}
  MITO_SAM2_CUDA_DEVICE=${rec_sam2_device}
  MITO_TRACK_PLAN_MAX_VOXELS=${rec_track_voxels}
  MITO_SAM2_XY_PAD=${rec_xy_pad}
  MITO_SAM2_XY_MIN=${rec_xy_min}
  MITO_SAM2_XY_MAX=${rec_xy_max}
  OMP_NUM_THREADS=${rec_omp}
  MITO_DEPS=${rec_deps}  (build arg — rebuild image to change)

Notes
  - On a single GPU, keep GUNICORN_WORKERS low: each worker loads SAM2 separately.
  - With 2+ GPUs, SAM2 uses device 0; the others are free for other work.
  - Raise MITO_TRACK_PLAN_MAX_VOXELS only when RAM headroom is confirmed.
  - Run: ops/docker/detect-hardware.sh --apply .env.docker
EOF
    ;;
esac
