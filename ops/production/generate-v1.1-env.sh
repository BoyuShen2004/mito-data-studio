#!/usr/bin/env bash
set -euo pipefail

mode=${1:?usage: generate-v1.1-env.sh staging|production CHECKOUT DATA_ROOT DB_NAME DB_USER BIND RELEASE}
checkout=${2:?missing checkout}
data_root=${3:?missing data root}
db_name=${4:?missing database name}
db_user=${5:?missing database user}
bind=${6:?missing bind}
release=${7:?missing release}

case "$mode" in
  staging|production) ;;
  *) echo "mode must be staging or production" >&2; exit 2 ;;
esac

env_file="$checkout/.env"
template="$checkout/ops/production/v1.1.0.env.example"
if [[ -e "$env_file" ]]; then
  echo "Refusing to overwrite $env_file" >&2
  exit 1
fi
test -f "$template"
test -d "$data_root"
checkout=$(realpath -e "$checkout")
data_root=$(realpath -e "$data_root")

# Keep this material byte-for-byte aligned with core.deployment.fingerprint().
# It is safe to compute before the database exists because the fingerprint is
# an identity of configured write targets, not a connectivity probe.
identity_material="$checkout|$data_root|postgresql|$db_name|127.0.0.1|5433"
identity_fingerprint=$(printf '%s' "$identity_material" | sha256sum | cut -c1-12)

umask 077
tmp=$(mktemp "$checkout/.env.tmp.XXXXXX")
trap 'rm -f -- "$tmp"' EXIT
db_password=$(openssl rand -hex 32)
django_key=$(openssl rand -hex 64)
metrics_token=$(openssl rand -hex 32)

{
  printf 'DJANGO_SECRET_KEY=%s\n' "$django_key"
  printf 'MITO_METRICS_BEARER_TOKEN=%s\n' "$metrics_token"
  printf 'MITO_DB_NAME=%s\n' "$db_name"
  printf 'MITO_DB_USER=%s\n' "$db_user"
  printf 'MITO_DB_PASSWORD=%s\n' "$db_password"
  printf 'MITO_DB_HOST=127.0.0.1\n'
  printf 'MITO_DB_PORT=5433\n'
  printf 'MITO_DATA_ROOT=%s\n' "$data_root"
  printf 'MITO_SERVICE_BIND=%s\n' "$bind"
  printf 'MITO_RELEASE=%s\n' "$release"
  printf 'MITO_EXPECTED_CHECKOUT=%s\n' "$checkout"
  printf 'MITO_EXPECTED_DATA_ROOT=%s\n' "$data_root"
  printf 'MITO_EXPECTED_DB_NAME=%s\n' "$db_name"
  printf 'MITO_EXPECTED_BIND=%s\n' "$bind"
  printf 'MITO_EXPECTED_FINGERPRINT=%s\n' "$identity_fingerprint"
  cat "$template"
} >"$tmp"

chmod 0600 "$tmp"
mv "$tmp" "$env_file"
trap - EXIT
echo "Created protected $mode v1.1 environment (secrets not displayed)."
