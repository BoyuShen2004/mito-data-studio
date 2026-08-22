#!/usr/bin/env bash
#
# setup.sh — verify/prepare everything Mito Data Studio needs to run.
#
#   conda activate mito-data-studio
#   scripts/dev/setup.sh                 # check + migrate (does NOT pip/conda-install)
#   scripts/dev/setup.sh --install-deps  # also pip-install missing light packages
#   scripts/dev/setup.sh --check-git     # fail if data/ DB / .env are tracked
#
# Safe for a mature conda env by default: it never runs `conda install` /
# `conda env update`, never overwrites an existing `.env`, and only runs
# `npm ci` when frontend lockfiles changed or node_modules is missing.
# Django `migrate` is the only DB write (additive schema updates).
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

CONDA_ENV_NAME="mito-data-studio"
CACHE_DIR="$REPO_ROOT/.dev-cache"
FRONTEND_DEPS_MARKER="$CACHE_DIR/frontend-deps.sha256"
AUTO_INSTALL=0

SMOKE=0

for arg in "$@"; do
  case "$arg" in
    --install-deps) AUTO_INSTALL=1 ;;
    --no-install)   AUTO_INSTALL=0 ;;  # kept for compatibility; default is already off
    --smoke)        SMOKE=1 ;;
  esac
done

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'
  YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi
info()  { printf '%s\n' "${BLUE}==>${RESET} $*"; }
ok()    { printf '%s\n' "${GREEN}  ✓${RESET} $*"; }
warn()  { printf '%s\n' "${YELLOW}  !${RESET} $*" >&2; }
die()   { printf '%s\n' "${RED}error:${RESET} $*" >&2; exit 1; }

# --- optional: git hygiene -------------------------------------------------
if [[ "${1:-}" == "--check-git" || "${1:-}" == "check-git" ]]; then
  cd "$(git rev-parse --show-toplevel)"
  FORBIDDEN='(^|/)data/|\.sqlite3(-journal)?$|(^|/)\.env$|\.(tif|tiff|npy|nii)(\.gz)?$'
  offenders="$( { git ls-files; git diff --cached --name-only; } \
      | sort -u | grep -E "$FORBIDDEN" || true )"
  if [[ -n "$offenders" ]]; then
    echo "ERROR: runtime data / DB / secrets must not be committed or pushed:" >&2
    echo "$offenders" | sed 's/^/  /' >&2
    echo >&2
    echo "Keep MITO_DATA_ROOT / sqlite / .env local. Vendor weights under" >&2
    echo "vendor/ use Git LFS and are allowed. See README.md." >&2
    exit 1
  fi
  echo "OK: no runtime data, dev DB, secrets, or volume binaries tracked or staged."
  exit 0
fi

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: scripts/dev/setup.sh [--install-deps] [--smoke] [--check-git]

  (default)        Check tools/deps/vendor, create .env only if missing,
                   npm only if needed, then Django check + migrate.
                   Does NOT pip/conda-install into your environment.
  --install-deps   Also pip-install missing light packages (hydra, onnxruntime, …).
                   Still never runs conda env update / pytorch install.
  --smoke          After setup, prove the install actually works: load the AI
                   runtimes, open a vendor model, and build the frontend.
                   Slower (~1 min); use it once on a fresh server.
  --check-git      Fail if data/ DB / .env / volume binaries are tracked
EOF
  exit 0
fi

# --- 1. Required tools -----------------------------------------------------
command -v python >/dev/null 2>&1 || die "python not found — fix: conda activate ${CONDA_ENV_NAME}"
command -v node   >/dev/null 2>&1 || die "node not found — fix: conda activate ${CONDA_ENV_NAME}"
command -v npm    >/dev/null 2>&1 || die "npm not found — fix: conda activate ${CONDA_ENV_NAME}"
PYTHON_VER="$(python --version 2>&1 | awk '{print $2}')"

# environment.yml pins python=3.11. Warn loudly off it: Django 5.1 breaks on
# 3.14 inside template rendering, which surfaces as
#   AttributeError: 'super' object has no attribute 'dicts'
# from django/template/context.py — every admin page 500s with a traceback that
# points at Django internals and says nothing about the interpreter. Reproduced
# on 3.14.6; a warning here costs nothing and saves that hunt.
PY_MAJOR_MINOR="${PYTHON_VER%.*}"
if [[ "$PY_MAJOR_MINOR" != "3.11" ]]; then
  warn "python ${PYTHON_VER} — environment.yml pins 3.11; admin pages are known to break on 3.14 (fix: conda activate ${CONDA_ENV_NAME})"
fi

if [[ "${CONDA_DEFAULT_ENV:-}" != "$CONDA_ENV_NAME" ]]; then
  warn "conda env '${CONDA_ENV_NAME}' not active (current: '${CONDA_DEFAULT_ENV:-none}') — fix: conda activate ${CONDA_ENV_NAME}"
fi

# --- 2. Backend / AI dependencies ------------------------------------------
# Core = needed for Django + EfficientSAM Point/Box Mask.
# Track helpers = SAM2 (hydra/iopath/tqdm); torch is separate (conda, heavy).

DEP_REPORT="$(
AUTO_INSTALL="$AUTO_INSTALL" REPO_ROOT="$REPO_ROOT" python - <<'PY'
import importlib, os, re, subprocess, sys

CORE = [
    ("django", "Django>=5.0,<5.2"),
    ("rest_framework", "djangorestframework>=3.15"),
    ("corsheaders", "django-cors-headers>=4.3"),
    ("dotenv", "python-dotenv"),
    # In MIDDLEWARE unconditionally: without it the WSGI application cannot be
    # imported and the server does not start.
    ("whitenoise", "whitenoise>=6.0"),
    ("numpy", "numpy"),
    ("tifffile", "tifffile"),
    ("PIL", "Pillow>=10"),
    ("onnxruntime", "onnxruntime>=1.17"),
    ("skimage", "scikit-image>=0.22"),
    ("scipy", "scipy>=1.11"),
]


def _db_engine():
    """Which database engine this checkout is configured for.

    Read from .env directly rather than the environment: this check runs
    before section 3 creates .env, so the variable is not exported yet.
    Unset means the settings default, which is sqlite.
    """
    env_path = os.path.join(os.environ.get("REPO_ROOT", "."), ".env")
    try:
        with open(env_path) as fh:
            for line in fh:
                m = re.match(r"\s*MITO_DB_ENGINE\s*=\s*(\S+)", line)
                if m:
                    return m.group(1).strip().strip("\"'").lower()
    except OSError:
        pass
    return "sqlite"


# The PostgreSQL driver is required only when the postgres engine is selected;
# the sqlite fallback needs nothing extra. Checking it conditionally keeps a
# sqlite-only checkout from being told to install a driver it will never load,
# while making the missing-driver failure explicit for everyone else — that
# omission previously surfaced as ModuleNotFoundError on the first `migrate`.
if _db_engine() in ("postgres", "postgresql"):
    CORE.append(("psycopg", "psycopg[binary]>=3.2"))
TRACK_PIP = [
    ("hydra", "hydra-core>=1.3.2"),
    ("iopath", "iopath>=0.1.10"),
    ("tqdm", "tqdm>=4.66.1"),
]

def missing(pairs):
    out = []
    for mod, spec in pairs:
        try:
            importlib.import_module(mod)
        except ImportError:
            out.append((mod, spec))
    return out

auto = os.environ.get("AUTO_INSTALL", "1") == "1"
core_miss = missing(CORE)
track_miss = missing(TRACK_PIP)
to_pip = [spec for _, spec in core_miss + track_miss if spec]

if auto and to_pip:
    print(f"==> Installing missing pip packages: {' '.join(to_pip)}", file=sys.stderr, flush=True)
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", *to_pip],
            stdout=sys.stderr,
        )
    except subprocess.CalledProcessError as exc:
        print(f"PIP_FAIL {exc.returncode}", flush=True)
        sys.exit(1)
    core_miss = missing(CORE)
    track_miss = missing(TRACK_PIP)

# Machine-readable status on stdout (parsed by the shell wrapper).
if core_miss:
    mods = ",".join(m for m, _ in core_miss)
    specs = " ".join(s for _, s in core_miss)
    print(f"CORE_FAIL mods={mods} specs={specs}", flush=True)
    sys.exit(2)
print("CORE_OK", flush=True)
if track_miss:
    mods = ",".join(m for m, _ in track_miss)
    specs = " ".join(s for _, s in track_miss)
    print(f"TRACK_FAIL mods={mods} specs={specs}", flush=True)
else:
    print("TRACK_OK", flush=True)
try:
    import torch
    print(f"TORCH_OK {torch.__version__} cuda={torch.cuda.is_available()}", flush=True)
except ImportError as exc:
    msg = str(exc).replace("\n", " ")
    if "iJIT_NotifyEvent" in msg or "iJIT_IsProfilingActive" in msg:
        print("TORCH_MKL_FAIL", flush=True)
    else:
        print("TORCH_FAIL", flush=True)
PY
)" || dep_rc=$?
dep_rc="${dep_rc:-0}"

if [[ "$dep_rc" -eq 2 ]]; then
  # Prefer the detailed CORE_FAIL line from the report when present.
  :
elif [[ "$dep_rc" -ne 0 ]]; then
  die "dependency check failed (exit ${dep_rc})"
fi

TORCH_NOTE=""
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  case "$line" in
    CORE_OK) ;;
    CORE_FAIL\ *)
      mods="${line#*mods=}"; mods="${mods%% specs=*}"
      specs="${line#*specs=}"
      die "core deps missing (${mods}) — fix: pip install ${specs}"
      ;;
    TRACK_OK) ;;
    TRACK_FAIL\ *)
      specs="${line#*specs=}"
      warn "SAM2 helpers missing (optional, Track falls back to local) — fix: pip install ${specs}"
      ;;
    TORCH_OK\ *)
      if [[ "$line" == *"cuda=False"* ]]; then
        TORCH_NOTE="torch CPU-only — Track will be slow (set MITO_TRACKING_PROVIDER=local, or match pytorch-cuda in environment.yml to your GPU driver)"
      fi
      ;;
    TORCH_MKL_FAIL)
      TORCH_NOTE="torch installed but fails to import (MKL incompatible) — fix: conda env update -f environment.yml --prune  (pins mkl<2024; see README)"
      ;;
    TORCH_FAIL)
      TORCH_NOTE="torch not installed — Track falls back to CPU (fix: conda env update -f environment.yml --prune)"
      ;;
    PIP_FAIL*)
      die "pip install of missing packages failed — fix: pip install the packages above manually"
      ;;
  esac
done <<< "$DEP_REPORT"

if [[ "$dep_rc" -eq 2 ]]; then
  die "core deps missing (see message above)"
fi
[[ -n "$TORCH_NOTE" ]] && warn "$TORCH_NOTE"

# --- 2b. Vendored model weights (Git LFS) ----------------------------------
EFF_ENC="$REPO_ROOT/vendor/efficient_sam/efficient_sam_vits_encoder.onnx"
SAM2_CKPT="$REPO_ROOT/vendor/sam2/checkpoints/sam2.1_hiera_large.pt"
lfs_hint() {
  if command -v git-lfs >/dev/null 2>&1 || git lfs version >/dev/null 2>&1; then
    printf '%s' "Run: git lfs install && git lfs pull"
  else
    printf '%s' "git-lfs is not installed. Install it (conda install -c conda-forge git-lfs, apt install git-lfs, or brew install git-lfs), then: git lfs install && git lfs pull"
  fi
}
if [[ ! -f "$EFF_ENC" ]] || [[ ! -f "$SAM2_CKPT" ]]; then
  die "vendor model weights missing ($(basename "$EFF_ENC") / $(basename "$SAM2_CKPT")). $(lfs_hint)"
fi
eff_sz=$(wc -c <"$EFF_ENC" | tr -d ' ')
sam_sz=$(wc -c <"$SAM2_CKPT" | tr -d ' ')
if [[ "$eff_sz" -lt 1000000 ]] || [[ "$sam_sz" -lt 1000000 ]]; then
  die "vendor weights are Git LFS pointer files, not the real weights (${eff_sz} / ${sam_sz} bytes). $(lfs_hint)"
fi
# --- 3. Local configuration (.env) -----------------------------------------
if [[ ! -f "$REPO_ROOT/.env" ]]; then
  cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
  ok ".env created from .env.example — review MITO_DATA_ROOT before registering real data"
fi

# An .env written before the in-app viewer existed can still say `placeholder`
# here, which silently turns View / Annotate into "no provider configured" —
# the app looks broken with nothing in the logs to explain it. Never rewrite
# the user's .env; just say exactly what to change.
PLACEHOLDER_VARS=""
for provider_var in MITO_VISUALIZATION_PROVIDER MITO_PROOFREADING_PROVIDER; do
  if grep -qE "^${provider_var}=placeholder" "$REPO_ROOT/.env" 2>/dev/null; then
    PLACEHOLDER_VARS="${PLACEHOLDER_VARS:+$PLACEHOLDER_VARS, }${provider_var}"
  fi
done
[[ -n "$PLACEHOLDER_VARS" ]] && warn "${PLACEHOLDER_VARS}=placeholder in .env — View/Annotate show 'no provider' — fix: set to 'inapp' in .env"

# MITO_DATA_ROOT must exist before anything can be registered into it.
DATA_ROOT="$(grep -E '^MITO_DATA_ROOT=' "$REPO_ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"
if [[ -n "$DATA_ROOT" ]]; then
  [[ "$DATA_ROOT" = /* ]] || DATA_ROOT="$REPO_ROOT/$DATA_ROOT"
  if [[ ! -d "$DATA_ROOT" ]]; then
    mkdir -p "$DATA_ROOT" || die "could not create MITO_DATA_ROOT (${DATA_ROOT}) — fix: check the path in .env"
    ok "MITO_DATA_ROOT created: $DATA_ROOT"
  fi
  [[ -w "$DATA_ROOT" ]] || die "MITO_DATA_ROOT (${DATA_ROOT}) is not writable — fix: chmod u+w ${DATA_ROOT}"
fi

# --- 4. Frontend dependencies ----------------------------------------------
mkdir -p "$CACHE_DIR"
current_deps_hash() {
  cat "$REPO_ROOT/frontend/package.json" "$REPO_ROOT/frontend/package-lock.json" 2>/dev/null \
    | sha256sum | awk '{print $1}'
}
DEPS_HASH="$(current_deps_hash)"
need_install=0
if [[ ! -d "$REPO_ROOT/frontend/node_modules" ]]; then
  need_install=1
elif [[ ! -f "$FRONTEND_DEPS_MARKER" ]] || [[ "$(cat "$FRONTEND_DEPS_MARKER")" != "$DEPS_HASH" ]]; then
  need_install=1
fi

if [[ "$need_install" -eq 1 ]]; then
  if [[ -f "$REPO_ROOT/frontend/package-lock.json" ]]; then
    npm ci --prefix "$REPO_ROOT/frontend"
  else
    npm install --prefix "$REPO_ROOT/frontend"
  fi
  current_deps_hash > "$FRONTEND_DEPS_MARKER"
  ok "frontend dependencies installed"
fi

# --- 5. Django checks + migrations -----------------------------------------
CHECK_OUT="$(python "$REPO_ROOT/manage.py" check 2>&1)" || {
  printf '%s\n' "$CHECK_OUT" >&2
  die "django check failed — fix: see errors above"
}
MIGRATE_OUT="$(python "$REPO_ROOT/manage.py" migrate --noinput 2>&1)" || {
  printf '%s\n' "$MIGRATE_OUT" >&2
  die "migration failed — fix: see errors above"
}
[[ "$MIGRATE_OUT" == *"No migrations to apply"* ]] || ok "migrations applied"

# --- 6. Optional smoke test (--smoke) --------------------------------------
# "The checks passed" and "the app actually works here" are different claims:
# this proves the AI runtimes load, a vendor weight file really opens (not just
# "is big enough"), and the frontend builds on this machine.
if [[ "$SMOKE" -eq 1 ]]; then
  info "Smoke test: loading AI runtimes and a vendor model"
  python - "$REPO_ROOT" <<'PY' || die "smoke test failed — see the error above."
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "backend"))
import onnxruntime

# The app's own session options (thread count from the real CPU budget) — using
# onnxruntime's defaults here would flood a cgroup-limited node with
# `pthread_setaffinity_np failed` noise that has nothing to do with the app.
from annotation.cellable_port.ai.efficient_sam import _session_options

encoder = root / "vendor/efficient_sam/efficient_sam_vits_encoder.onnx"
session = onnxruntime.InferenceSession(str(encoder), sess_options=_session_options())
assert session.get_inputs(), "EfficientSAM encoder loaded but exposes no inputs"
print(f"  EfficientSAM encoder OK ({encoder.name})")

try:
    import torch

    print(f"  torch {torch.__version__} OK (cuda={torch.cuda.is_available()})")
except ImportError as exc:
    if "iJIT_NotifyEvent" in str(exc) or "iJIT_IsProfilingActive" in str(exc):
        print("  torch import failed (MKL incompatible) — fix: conda env update -f environment.yml --prune")
    else:
        print("  torch not installed — SAM2 Track will fall back to local CPU")
PY
  ok "AI runtimes load"

  info "Smoke test: building the frontend"
  npm run build --prefix "$REPO_ROOT/frontend" >/dev/null \
    || die "frontend build failed — see 'npm run build --prefix frontend'."
  ok "frontend builds"
fi

printf '\n'
ok "setup ok — python ${PYTHON_VER%.*}, django ok, frontend ok, migrate ok"
printf '  next: make dev\n'
