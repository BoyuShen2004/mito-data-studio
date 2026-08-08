#!/usr/bin/env bash
#
# dev-launch.sh — start Mito Data Agent (Django + Vite).
#
#   ./dev-launch.sh
#
# Assumes ./dev-setup.sh has already been run. Always frees ports left by a
# previous launch on this node, then starts both servers and stops them
# together on one Ctrl+C.
#
# On a SLURM compute node: reverse-tunnels ports to the login node when
# possible (LOGIN_NODE / SLURM_SUBMIT_HOST / scontrol AllocNode).
#
#   VITE_HOST=0.0.0.0 DJANGO_HOST=0.0.0.0 NO_BROWSER=1 ./dev-launch.sh
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

CONDA_ENV_NAME="mito-data-agent"

REMOTE_DEV=0
if [[ -n "${VSCODE_IPC_HOOK_CLI:-}" || -n "${SSH_CONNECTION:-}" ]]; then
  REMOTE_DEV=1
fi

DJANGO_HOST="${DJANGO_HOST:-127.0.0.1}"
DJANGO_PORT="${DJANGO_PORT:-8000}"
VITE_HOST="${VITE_HOST:-127.0.0.1}"
VITE_PORT="${VITE_PORT:-5173}"
NO_BROWSER="${NO_BROWSER:-1}"

if [[ "$REMOTE_DEV" -eq 1 ]]; then
  [[ "${DJANGO_HOST}" == "127.0.0.1" ]] && DJANGO_HOST="0.0.0.0"
  [[ "${VITE_HOST}" == "127.0.0.1" ]] && VITE_HOST="0.0.0.0"
fi

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      cat <<'EOF'
Usage: ./dev-launch.sh

  Frees Django/Vite ports from a previous launch, then starts both servers.
  Ctrl+C stops this launch cleanly.

Env: DJANGO_HOST DJANGO_PORT VITE_HOST VITE_PORT NO_BROWSER LOGIN_NODE
EOF
      exit 0
      ;;
  esac
done

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
  YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi
info()  { printf '%s\n' "${BLUE}==>${RESET} $*"; }
ok()    { printf '%s\n' "${GREEN}  ✓${RESET} $*"; }
warn()  { printf '%s\n' "${YELLOW}  !${RESET} $*" >&2; }
die()   { printf '%s\n' "${RED}error:${RESET} $*" >&2; exit 1; }

command -v python >/dev/null 2>&1 || die "python not found. Activate the '${CONDA_ENV_NAME}' conda environment first."
command -v npm    >/dev/null 2>&1 || die "npm not found. Activate the '${CONDA_ENV_NAME}' conda environment first."
[[ -f "$REPO_ROOT/.env" ]] || die "No .env found. Run ./dev-setup.sh first."
[[ -d "$REPO_ROOT/frontend/node_modules" ]] || die "Frontend dependencies not installed. Run ./dev-setup.sh first."

PYTHON_BIN="$(command -v python)"
PYTHON_VERSION="$("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
PYTHON_MAJOR="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info[0])')"
if [[ "$PYTHON_MAJOR" -lt 3 ]]; then
  die "python resolves to ${PYTHON_BIN} (${PYTHON_VERSION}, needs Python 3). Activate the '${CONDA_ENV_NAME}' conda environment first."
fi

port_pids() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -tlnp 2>/dev/null | awk -v p=":$port" '
      index($4, p) {
        while (match($0, /pid=[0-9]+/)) {
          print substr($0, RSTART+4, RLENGTH-4)
          $0 = substr($0, RSTART+RLENGTH)
        }
      }' | sort -u
  elif command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  fi
}

free_port() {
  local port="$1" label="$2"
  local pids
  pids="$(port_pids "$port" | tr '\n' ' ')"
  pids="${pids%% }"
  pids="${pids## }"
  if [[ -z "$pids" ]]; then
    return 0
  fi
  warn "Freeing stale ${label} on :${port} (pids: ${pids})"
  # shellcheck disable=SC2086
  kill -TERM $pids 2>/dev/null || true
  sleep 1
  # shellcheck disable=SC2086
  kill -KILL $pids 2>/dev/null || true
  sleep 0.3
  if [[ -n "$(port_pids "$port")" ]]; then
    die "Could not free port ${port}. Free it manually, then re-run."
  fi
  ok "Port ${port} is free."
}

info "Clearing ports ${DJANGO_PORT} / ${VITE_PORT} if still held by a previous launch…"
free_port "$DJANGO_PORT" "Django"
free_port "$VITE_PORT" "Vite"

SETSID=""
command -v setsid >/dev/null 2>&1 && SETSID="setsid"

BACKEND_PID=""
FRONTEND_PID=""
TUNNEL_PID=""

stop_proc() {
  local pid="$1"
  [[ -z "$pid" ]] && return 0
  if [[ -n "$SETSID" ]]; then
    kill -TERM -- "-$pid" 2>/dev/null || true
  else
    kill -TERM "$pid" 2>/dev/null || true
  fi
}

CLEANED=""
cleanup() {
  [[ -n "$CLEANED" ]] && return 0
  CLEANED=1
  [[ -n "$TUNNEL_PID" ]] && kill "$TUNNEL_PID" 2>/dev/null
  stop_proc "$FRONTEND_PID"
  stop_proc "$BACKEND_PID"
  wait 2>/dev/null || true
}

on_signal() {
  trap - INT TERM
  printf '\n'
  info "Shutting down…"
  cleanup
  ok "Stopped."
  exit 0
}
trap on_signal INT TERM
trap cleanup EXIT

export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-*}"
export PYTHONUNBUFFERED=1

# Strip runserver's fixed startup banner (Watching for changes / system checks /
# version / "Starting development server at" / "Quit the server"); real errors
# and request logs use other lines and pass through untouched.
DJANGO_BANNER_FILTER=(
  -e '^Watching for file changes with StatReloader$'
  -e '^Performing system checks\.\.\.$'
  -e '^System check identified no issues'
  -e '^[A-Z][a-z]+ [0-9]{1,2}, [0-9]{4} - [0-9]{2}:[0-9]{2}:[0-9]{2}$'
  -e '^Django version .*, using settings'
  -e '^Starting development server at '
  -e '^Quit the server with CONTROL-C\.$'
  -e '^$'
)

info "Starting Django backend on http://${DJANGO_HOST}:${DJANGO_PORT}"
$SETSID python "$REPO_ROOT/backend/manage.py" runserver "${DJANGO_HOST}:${DJANGO_PORT}" \
  > >(grep --line-buffered -Ev "${DJANGO_BANNER_FILTER[@]}") \
  2> >(grep --line-buffered -Ev "${DJANGO_BANNER_FILTER[@]}" >&2) &
BACKEND_PID=$!

export VITE_BACKEND_URL="http://127.0.0.1:${DJANGO_PORT}"
export VITE_HOST VITE_PORT

info "Starting React frontend on http://${VITE_HOST}:${VITE_PORT}"
$SETSID npm run dev --silent --prefix "$REPO_ROOT/frontend" -- --host "$VITE_HOST" --port "$VITE_PORT" --strictPort --logLevel error &
FRONTEND_PID=$!

sleep 2

if [[ "$REMOTE_DEV" -eq 1 ]]; then
  server_ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  if [[ -z "$server_ip" ]]; then
    server_ip="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo localhost)"
  fi
  APP_URL="http://${server_ip}:${VITE_PORT}/login"
else
  APP_URL="http://127.0.0.1:${VITE_PORT}/login"
fi

if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
  wait "$BACKEND_PID" 2>/dev/null
  die "Django backend failed to start — see the output above."
fi
if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
  wait "$FRONTEND_PID" 2>/dev/null
  die "Vite frontend failed to start — see the output above."
fi

detect_login_node() {
  if [[ -n "${LOGIN_NODE:-}" ]]; then
    printf '%s' "$LOGIN_NODE"; return 0
  fi
  if [[ -n "${SLURM_SUBMIT_HOST:-}" ]]; then
    printf '%s' "$SLURM_SUBMIT_HOST"; return 0
  fi
  if [[ -n "${SLURM_JOB_ID:-}" ]] && command -v scontrol >/dev/null 2>&1; then
    local alloc
    alloc="$(scontrol show job "$SLURM_JOB_ID" 2>/dev/null \
      | grep -oP 'AllocNode:Sid=\K[^:]+' 2>/dev/null | head -1 || true)"
    if [[ -n "$alloc" ]]; then
      printf '%s' "$alloc"; return 0
    fi
  fi
  return 1
}

CURRENT_HOST="$(hostname 2>/dev/null || true)"
CURRENT_HOST_SHORT="$(hostname -s 2>/dev/null || true)"
LOGIN_HOST=""
if detect_login_node_result="$(detect_login_node)"; then
  LOGIN_HOST="$detect_login_node_result"
fi

if [[ -n "$LOGIN_HOST" && "$LOGIN_HOST" != "$CURRENT_HOST" && "$LOGIN_HOST" != "$CURRENT_HOST_SHORT" ]] \
  && command -v ssh >/dev/null 2>&1; then
  info "On a compute node (${CURRENT_HOST:-this host}) — bridging ports to ${LOGIN_HOST}…"
  TUNNEL_ERR="$(mktemp 2>/dev/null || echo /tmp/mito-tunnel-err.$$)"
  ssh -N -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes \
    -R "${VITE_PORT}:localhost:${VITE_PORT}" \
    -R "${DJANGO_PORT}:localhost:${DJANGO_PORT}" \
    "$LOGIN_HOST" 2>"$TUNNEL_ERR" &
  TUNNEL_PID=$!
  sleep 2
  if kill -0 "$TUNNEL_PID" 2>/dev/null; then
    if ssh -o BatchMode=yes -o ConnectTimeout=5 "$LOGIN_HOST" \
         "curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:${VITE_PORT}/" 2>/dev/null \
         | grep -qE '^(2|3)[0-9][0-9]$'; then
      ok "Bridged to ${LOGIN_HOST} — verified reachable from there. Open the app the same way you would from ${LOGIN_HOST}."
    else
      warn "SSH tunnel to ${LOGIN_HOST} is up, but end-to-end reachability not confirmed yet."
    fi
  else
    warn "Couldn't bridge to ${LOGIN_HOST}: $(tr -s '\n' ' ' < "$TUNNEL_ERR" 2>/dev/null | sed 's/ *$//')"
    warn "Open from ${LOGIN_HOST} directly, or set LOGIN_NODE=<host> if that's the wrong one."
    TUNNEL_PID=""
  fi
  rm -f "$TUNNEL_ERR"
elif [[ -z "$LOGIN_HOST" && -n "${SLURM_JOB_ID:-}" ]]; then
  warn "On a SLURM compute node but couldn't determine the login node. Set LOGIN_NODE=<host> if needed."
fi

printf '\n%s\n' "${BOLD}${GREEN}Mito Data Agent running${RESET}"
printf '  open:  %s%s%s\n' "$BOLD" "$APP_URL" "$RESET"
printf '  login: manager / demo12345\n'
printf '  Ctrl+C to stop\n\n'

EXIT_CODE=0
while true; do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    warn "Django backend exited unexpectedly."
    EXIT_CODE=1
    break
  fi
  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    warn "React frontend exited unexpectedly."
    EXIT_CODE=1
    break
  fi
  sleep 1
done

exit "$EXIT_CODE"
