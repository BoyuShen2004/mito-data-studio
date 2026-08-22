#!/usr/bin/env bash
set -euo pipefail

# Production-build, restored-data soak runner. It never starts a web server:
# Playwright targets the already isolated gunicorn service on 127.0.0.1:18189.
duration_seconds=${MITO_STAGING_SOAK_SECONDS:-300}
warmup_seconds=${MITO_STAGING_SOAK_WARMUP_SECONDS:-60}
staging_root=${MITO_STAGING_ROOT:-/home/weidf/shenb/mito-data-studio-staging-v1.1.0}
staging_bind=${MITO_STAGING_BIND:-127.0.0.1:18192}
staging_url=${MITO_STAGING_URL:-https://127.0.0.1:18194}
staging_service=${MITO_STAGING_SERVICE:-mito-data-studio-staging-v1.1.0.service}
staging_user=${MITO_STAGING_USER:-mito-staging-v11}
staging_home=${MITO_STAGING_HOME:-/home/mito-staging-v11}
evidence_dir=${MITO_SOAK_EVIDENCE_DIR:?set MITO_SOAK_EVIDENCE_DIR outside the repository}
credentials_file=${MITO_SOAK_CREDENTIALS_FILE:-$staging_root/run/.env.staging-soak-users}
workflow_credentials_file=${MITO_WORKFLOW_CREDENTIALS_FILE:-$staging_root/run/.env.staging-workflow-users}
comprehensive=${MITO_STAGING_COMPREHENSIVE:-0}

test "$(curl -ksS -o /dev/null -w '%{http_code}' "$staging_url/login")" = 200
test ! -e "$evidence_dir"
install -d -m 0700 "$evidence_dir"
if [[ "$comprehensive" == 1 ]]; then
  # Runtime credentials intentionally remain mode 0600 and owned by the
  # isolated service account; preflight them with that same identity.
  sudo -u "$staging_user" test -s "$workflow_credentials_file"
fi

metrics="$evidence_dir/system-metrics.tsv"
printf 'utc\tworkers\trss_kib\tpss_kib\tpss_anon_kib\tpss_file_kib\tcpu_percent\tpg_connections\trecv_q\tsend_q\tload1\n' > "$metrics"
access_start=$(sudo wc -l "$staging_root/logs/access.log" | awk '{print $1}')

sample_system() {
  local master workers rss pss pss_anon pss_file cpu pg queues load1 worker_pids
  master=$(systemctl show "$staging_service" -p MainPID --value)
  read -r workers rss cpu < <(
    ps --ppid "$master" -o rss=,%cpu= | awk \
      '{n += 1; rss += $1; cpu += $2} END {printf "%d %d %.2f\n", n, rss, cpu}'
  )
  worker_pids=$(pgrep -P "$master" | paste -sd, -)
  read -r pss pss_anon pss_file < <(
    sudo awk '
      /^Pss:/ {pss += $2}
      /^Pss_Anon:/ {anon += $2}
      /^Pss_File:/ {file += $2}
      END {printf "%d %d %d\n", pss, anon, file}
    ' $(printf '/proc/%s/smaps_rollup ' ${worker_pids//,/ })
  )
  pg=$(sudo -u "$staging_user" bash -c '
    set -a; source "'$staging_root'/.env"; set +a
    cd "'$staging_root'"
    venv/bin/python manage.py shell -c \
      "from django.db import connection; c=connection.cursor(); c.execute(\"select count(*) from pg_stat_activity where datname=current_database()\"); print(c.fetchone()[0])"
  ' | tail -1)
  queues=$(ss -ltn "( sport = :${staging_bind##*:} )" | awk 'NR == 2 {print $2 " " $3}')
  load1=$(awk '{print $1}' /proc/loadavg)
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$workers" "$rss" "$pss" "$pss_anon" "$pss_file" "$cpu" "$pg" \
    "${queues%% *}" "${queues##* }" "$load1" >> "$metrics"
}

sample_system
(
  while :; do
    sleep 30
    sample_system
  done
) &
sampler_pid=$!
reloader_pid=
if [[ "$comprehensive" == 1 ]]; then
  (
    sleep $((warmup_seconds + duration_seconds / 2))
    before=$(systemctl show "$staging_service" -p MainPID --value)
    sudo systemctl reload "$staging_service"
    deadline=$((SECONDS + 60))
    while [[ $SECONDS -lt $deadline ]]; do
      current=$(systemctl show "$staging_service" -p MainPID --value)
      workers=$(pgrep -P "$current" | wc -l)
      if [[ "$current" == "$before" && "$workers" == 3 ]] && \
         [[ "$(curl -ksS -o /dev/null -w '%{http_code}' "$staging_url/readyz")" == 200 ]]; then
        printf '%s\tmaster=%s\tworkers=%s\tready=200\n' \
          "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$current" "$workers" \
          > "$evidence_dir/worker-reload.txt"
        exit 0
      fi
      sleep 1
    done
    printf 'worker reload failed readiness/deadline gate\n' > "$evidence_dir/worker-reload.txt"
    exit 1
  ) &
  reloader_pid=$!
fi
cleanup() {
  kill "$sampler_pid" 2>/dev/null || true
  wait "$sampler_pid" 2>/dev/null || true
  if [[ -n "$reloader_pid" ]]; then
    kill "$reloader_pid" 2>/dev/null || true
    wait "$reloader_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

set +e
sudo -u "$staging_user" env HOME="$staging_home" bash -c '
  set -euo pipefail
  set -a
  source "'$credentials_file'"
  if [[ "'$comprehensive'" == 1 ]]; then source "'$workflow_credentials_file'"; fi
  set +a
  if [[ "'$comprehensive'" == 1 ]]; then
    export MITO_STAGING_ASSIGNED_TASK_ID="$MITO_STAGING_SOAK_ASSIGNED_TASK_ID"
  fi
  cd "'$staging_root'/frontend"
  if [[ "'$comprehensive'" == 1 ]]; then
    MITO_STAGING_INTEGRATED_WORKFLOWS=1 \
    MITO_STAGING_SOAK_WORKFLOWS=1 \
    MITO_STAGING_ALLOW_WRITES=1 \
    MITO_STAGING_AI_WORKFLOWS=1 \
    MITO_STAGING_FAILURE_WORKFLOWS=1 \
    MITO_STAGING_BASE_URL="'$staging_url'" \
      npx playwright test --config=playwright.staging.config.ts --grep \
        "manager collaboration|integrated organization|full-task share|restored annotators|same-task stale|staging brush Save|autosave retries|enabled EfficientSAM"
  fi
  MITO_STAGING_SOAK_SECONDS="'$duration_seconds'" \
  MITO_STAGING_SOAK_WARMUP_SECONDS="'$warmup_seconds'" \
  MITO_STAGING_BASE_URL="'$staging_url'" \
    npx playwright test --config=playwright.staging.config.ts \
      --grep "configurable concurrent-user navigation soak"
  if [[ "'$comprehensive'" == 1 ]]; then
    MITO_STAGING_SOAK_WORKFLOWS=1 \
    MITO_STAGING_ALLOW_WRITES=1 \
    MITO_STAGING_AI_WORKFLOWS=1 \
    MITO_STAGING_FAILURE_WORKFLOWS=1 \
    MITO_STAGING_BASE_URL="'$staging_url'" \
      npx playwright test --config=playwright.staging.config.ts --grep \
        "manager collaboration|full-task share|restored annotators|same-task stale|staging brush Save|autosave retries|enabled EfficientSAM"
  fi
' 2>&1 | tee "$evidence_dir/playwright.log"
playwright_status=${PIPESTATUS[0]}
set -e

cleanup
trap - EXIT
sample_system
if [[ "$comprehensive" == 1 ]]; then
  test -s "$evidence_dir/worker-reload.txt"
  rg -q 'ready=200' "$evidence_dir/worker-reload.txt"
fi
access_end=$(sudo wc -l "$staging_root/logs/access.log" | awk '{print $1}')
sudo sed -n "$((access_start + 1)),${access_end}p" "$staging_root/logs/access.log" \
  > "$evidence_dir/access.log"
printf 'playwright_status=%s\naccess_start=%s\naccess_end=%s\n' \
  "$playwright_status" "$access_start" "$access_end" > "$evidence_dir/summary.txt"

unauthorized_count=$(awk '$9 == 401 {count += 1} END {print count + 0}' "$evidence_dir/access.log")
server_error_count=$(awk '$9 ~ /^5[0-9][0-9]$/ {count += 1} END {print count + 0}' "$evidence_dir/access.log")
printf 'unauthorized_count=%s\nserver_error_count=%s\n' \
  "$unauthorized_count" "$server_error_count" >> "$evidence_dir/summary.txt"
classification=$(bash "$(dirname "$0")/classify_soak_access_log.sh" "$evidence_dir/access.log")
printf '%s\n' "$classification" | tee -a "$evidence_dir/summary.txt"
unexpected_unauthorized_count=$(printf '%s\n' "$classification" | awk -F= '$1 == "unexpected_unauthorized_count" {print $2}')
if [[ "$unexpected_unauthorized_count" != 0 || "$server_error_count" != 0 ]]; then
  printf 'Soak access-log gate failed: unexpected_401=%s 5xx=%s\n' \
    "$unexpected_unauthorized_count" "$server_error_count" >&2
  exit 1
fi

exit "$playwright_status"
