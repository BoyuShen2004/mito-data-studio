#!/usr/bin/env bash
set -euo pipefail

# Generate staging-only credentials without printing them.  This script is
# deliberately fail-closed: an existing environment is never overwritten.
checkout=${1:-/home/weidf/shenb/mito-data-studio-staging-20260731}
postgres_env="$checkout/ops/staging/.env.postgres"
django_env="$checkout/.env"

if [[ -e "$postgres_env" || -e "$django_env" ]]; then
  echo "Refusing to overwrite an existing staging environment." >&2
  exit 1
fi

umask 077
db_password=$(openssl rand -hex 32)
django_key=$(openssl rand -hex 64)
postgres_tmp=$(mktemp "$checkout/ops/staging/.env.postgres.tmp.XXXXXX")
django_tmp=$(mktemp "$checkout/.env.tmp.XXXXXX")
trap 'rm -f -- "$postgres_tmp" "$django_tmp"' EXIT

{
  printf 'POSTGRES_DB=mito_staging\n'
  printf 'POSTGRES_USER=mito_staging\n'
  printf 'POSTGRES_PASSWORD=%s\n' "$db_password"
} >"$postgres_tmp"

sed \
  -e "/^MITO_DB_HOST=/a MITO_DB_USER=mito_staging\nMITO_DB_PASSWORD=$db_password" \
  -e "/^DJANGO_DEBUG=/a DJANGO_SECRET_KEY=$django_key" \
  "$checkout/ops/staging/staging.env.example" >"$django_tmp"

chmod 0600 "$postgres_tmp" "$django_tmp"
mv "$postgres_tmp" "$postgres_env"
mv "$django_tmp" "$django_env"
trap - EXIT

echo "Created protected staging environment files (values not displayed)."
