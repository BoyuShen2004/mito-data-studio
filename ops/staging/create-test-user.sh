#!/usr/bin/env bash
set -euo pipefail

checkout=${1:-/home/weidf/shenb/mito-data-studio-staging-20260731}
credentials="$checkout/run/.env.staging-test-user"
if [[ -e "$credentials" ]]; then
  echo "Refusing to overwrite existing staging test credentials." >&2
  exit 1
fi

umask 077
credentials_tmp=$(mktemp "$checkout/run/.env.staging-test-user.tmp.XXXXXX")
trap 'rm -f -- "$credentials_tmp"' EXIT
username=staging_release_manager
password=$(openssl rand -hex 24)
worker_a=staging_release_worker_a
worker_a_password=$(openssl rand -hex 24)
worker_b=staging_release_worker_b
worker_b_password=$(openssl rand -hex 24)
{
  printf 'MITO_STAGING_TEST_USERNAME=%s\n' "$username"
  printf 'MITO_STAGING_TEST_PASSWORD=%s\n' "$password"
  printf 'MITO_STAGING_WORKER_A_USERNAME=%s\n' "$worker_a"
  printf 'MITO_STAGING_WORKER_A_PASSWORD=%s\n' "$worker_a_password"
  printf 'MITO_STAGING_WORKER_B_USERNAME=%s\n' "$worker_b"
  printf 'MITO_STAGING_WORKER_B_PASSWORD=%s\n' "$worker_b_password"
} >"$credentials_tmp"

set -a
source "$checkout/.env"
source "$credentials_tmp"
set +a
"$checkout/venv/bin/python" "$checkout/backend/manage.py" shell -c '
import os
from django.contrib.auth import get_user_model
User = get_user_model()
accounts = (
    (os.environ["MITO_STAGING_TEST_USERNAME"], os.environ["MITO_STAGING_TEST_PASSWORD"]),
    (os.environ["MITO_STAGING_WORKER_A_USERNAME"], os.environ["MITO_STAGING_WORKER_A_PASSWORD"]),
    (os.environ["MITO_STAGING_WORKER_B_USERNAME"], os.environ["MITO_STAGING_WORKER_B_PASSWORD"]),
)
for username, password in accounts:
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@invalid.example"},
    )
    user.email = f"{username}@invalid.example"
    user.is_active = True
    user.is_staff = True
    user.is_superuser = True
    user.set_password(password)
    user.save()
'

chmod 0600 "$credentials_tmp"
mv "$credentials_tmp" "$credentials"
trap - EXIT
echo "Created three staging-only test identities (credentials not displayed)."
