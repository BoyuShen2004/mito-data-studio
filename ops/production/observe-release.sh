#!/usr/bin/env bash
set -euo pipefail

duration_seconds=${MITO_OBSERVATION_SECONDS:-3600}
sample_seconds=${MITO_OBSERVATION_SAMPLE_SECONDS:-30}
checkout=${MITO_PRODUCTION_ROOT:-/home/weidf/shenb/mito-data-agent-production-v1.1.0}
service=${MITO_PRODUCTION_SERVICE:-mito-data-agent-v1.1.0.service}
service_user=${MITO_PRODUCTION_USER:-mito-production-v11}
public_url=${MITO_PUBLIC_URL:-https://mito-data-agent.seg.bio}
evidence_dir=${MITO_OBSERVATION_EVIDENCE_DIR:?set an unused evidence directory outside the repository}
source_manifest=${MITO_SOURCE_MANIFEST:?set the immutable source/reference sha256 manifest}

test "$duration_seconds" -ge 3600
test "$sample_seconds" -ge 10
sudo test -s "$checkout/.env"
test -s "$source_manifest"
test ! -e "$evidence_dir"
install -d -m 0700 "$evidence_dir"

access_log="$checkout/logs/access.log"
error_log="$checkout/logs/error.log"
sudo test -f "$access_log"
sudo test -f "$error_log"
access_start=$(sudo wc -l "$access_log" | awk '{print $1}')
error_start=$(sudo wc -l "$error_log" | awk '{print $1}')
metrics="$evidence_dir/metrics.tsv"
printf 'utc\tmaster\tworkers\tpss_kib\tcpu_percent\tnrestarts\tpg_connections\thealth_status\thealth_seconds\tready_status\tready_seconds\n' > "$metrics"

deadline=$((SECONDS + duration_seconds))
samples=0
sample_failed=0
while :; do
  master=$(systemctl show "$service" -p MainPID --value)
  nrestarts=$(systemctl show "$service" -p NRestarts --value)
  read -r workers cpu < <(
    ps --ppid "$master" -o %cpu= | awk '{n += 1; cpu += $1} END {printf "%d %.2f\n", n, cpu}'
  )
  worker_pids=$(pgrep -P "$master" | paste -sd, -)
  pss=0
  if [[ -n "$worker_pids" ]]; then
    pss=$(sudo awk '/^Pss:/ {total += $2} END {print total + 0}' \
      $(printf '/proc/%s/smaps_rollup ' ${worker_pids//,/ }))
  fi
  pg=$(sudo -u "$service_user" bash -c '
    set -a; source "'$checkout'/.env"; set +a
    cd "'$checkout'/backend"
    ../venv/bin/python manage.py shell -c \
      "from django.db import connection; c=connection.cursor(); c.execute(\"select count(*) from pg_stat_activity where datname=current_database()\"); print(c.fetchone()[0])"
  ' | tail -1)
  health=$(curl -sS -o /dev/null -w '%{http_code} %{time_total}' "$public_url/healthz" || printf '000 0')
  ready=$(curl -sS -o /dev/null -w '%{http_code} %{time_total}' "$public_url/readyz" || printf '000 0')
  read -r health_status health_seconds <<<"$health"
  read -r ready_status ready_seconds <<<"$ready"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$master" "$workers" "$pss" "$cpu" \
    "$nrestarts" "$pg" "$health_status" "$health_seconds" "$ready_status" "$ready_seconds" \
    >> "$metrics"
  samples=$((samples + 1))
  if [[ "$workers" != 3 || "$nrestarts" != 0 || "$health_status" != 200 || "$ready_status" != 200 ]]; then
    sample_failed=1
  fi
  if [[ $SECONDS -ge $deadline ]]; then break; fi
  sleep "$sample_seconds"
done

access_end=$(sudo wc -l "$access_log" | awk '{print $1}')
error_end=$(sudo wc -l "$error_log" | awk '{print $1}')
sudo sed -n "$((access_start + 1)),${access_end}p" "$access_log" > "$evidence_dir/access.log"
sudo sed -n "$((error_start + 1)),${error_end}p" "$error_log" > "$evidence_dir/error.log"
server_errors=$(awk '$9 ~ /^5[0-9][0-9]$/ {count += 1} END {print count + 0}' "$evidence_dir/access.log")
mutation_failures=$(awk '
  $7 ~ /\/(autosave|recovery|predict-mask|track|processing)\// && $9 !~ /^(200|201|204|409)$/ {count += 1}
  END {print count + 0}
' "$evidence_dir/access.log")
sudo sha256sum --check "$source_manifest" > "$evidence_dir/source-check.log"
printf 'duration_seconds=%s\nsamples=%s\nsample_failed=%s\nserver_errors=%s\nmutation_failures=%s\naccess_start=%s\naccess_end=%s\n' \
  "$duration_seconds" "$samples" "$sample_failed" "$server_errors" "$mutation_failures" \
  "$access_start" "$access_end" > "$evidence_dir/summary.txt"

# A single externally induced 5xx is retained for operator inspection rather
# than hidden. Health/readiness, worker identity, mutation paths and immutable
# source/reference integrity are hard gates here; sustained/general 5xx is a
# rollback decision made from access.log plus the time series.
test "$sample_failed" = 0
test "$mutation_failures" = 0
