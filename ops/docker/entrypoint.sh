#!/usr/bin/env bash
# Container entrypoint. Usage: entrypoint.sh <serve|migrate|manage ...|shell>
#
# `serve` (the default) waits for the database, applies migrations, collects
# static files and execs gunicorn.
set -euo pipefail

cd /app/backend

log() { printf '[entrypoint] %s\n' "$*" >&2; }

# Block until Django can actually open a connection. Compose's `depends_on:
# service_healthy` already covers the common case, but this also covers an
# external/managed database that is reachable-but-not-ready, and costs nothing
# when the database is already up.
wait_for_db() {
  if [ "${MITO_DB_ENGINE:-sqlite}" = "sqlite" ]; then
    return 0
  fi
  local attempts="${MITO_DB_WAIT_ATTEMPTS:-60}"
  local i=1
  while [ "$i" -le "$attempts" ]; do
    if python -c "
import django, os, sys
django.setup()
from django.db import connection
try:
    connection.ensure_connection()
except Exception:
    sys.exit(1)
" 2>/dev/null; then
      log "database is up"
      return 0
    fi
    log "waiting for database (${i}/${attempts})"
    sleep 2
    i=$((i + 1))
  done
  log "ERROR: database not reachable after ${attempts} attempts"
  return 1
}

prepare() {
  wait_for_db

  # Idempotent, and cheap when there is nothing to do. Set MITO_SKIP_MIGRATE=1
  # if you run migrations as a separate deploy step (e.g. several app replicas
  # against one database, where concurrent migrate calls would race).
  if [ "${MITO_SKIP_MIGRATE:-0}" != "1" ]; then
    log "applying migrations"
    python manage.py migrate --noinput
  fi

  # Django's own admin/DRF assets. The SPA is served by WhiteNoise straight
  # from /app/frontend/dist (WHITENOISE_ROOT) and is not involved here.
  if [ "${MITO_SKIP_COLLECTSTATIC:-0}" != "1" ]; then
    log "collecting static files"
    python manage.py collectstatic --noinput --clear >/dev/null
  fi

  # Optional first-run admin. Skipped silently unless both variables are set,
  # and never overwrites an existing user — so leaving these in the env file
  # across restarts is safe, and rotating the password there does nothing.
  if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
    log "ensuring superuser '${DJANGO_SUPERUSER_USERNAME}' exists"
    python manage.py createsuperuser --noinput \
      --username "${DJANGO_SUPERUSER_USERNAME}" \
      --email "${DJANGO_SUPERUSER_EMAIL:-admin@example.com}" 2>/dev/null \
      || log "superuser already exists; leaving it untouched"
  fi
}

case "${1:-serve}" in
  serve)
    prepare
    log "starting gunicorn on 0.0.0.0:${PORT:-8000}"
    # --timeout 300: slice reads touch multi-gigabyte TIFFs and the 30s default
    # kills them mid-request. Logs go to stdout/stderr for `docker compose logs`.
    exec gunicorn config.wsgi:application \
      --bind "0.0.0.0:${PORT:-8000}" \
      --workers "${GUNICORN_WORKERS:-3}" \
      --threads "${GUNICORN_THREADS:-2}" \
      --timeout "${GUNICORN_TIMEOUT:-300}" \
      --graceful-timeout 30 \
      --access-logfile - \
      --error-logfile - \
      --capture-output
    ;;
  migrate)
    wait_for_db
    exec python manage.py migrate --noinput
    ;;
  manage)
    shift
    wait_for_db
    exec python manage.py "$@"
    ;;
  shell)
    exec /bin/bash
    ;;
  *)
    # Anything else is run verbatim, so `docker compose run app <cmd>` works.
    exec "$@"
    ;;
esac
