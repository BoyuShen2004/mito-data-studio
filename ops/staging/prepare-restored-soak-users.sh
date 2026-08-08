#!/usr/bin/env bash
set -euo pipefail

checkout=${1:-/home/weidf/shenb/mito-data-agent-staging-v1.1.0}
credentials="$checkout/run/.env.staging-soak-users"
if [[ -e "$credentials" ]]; then
  echo "Refusing to overwrite existing staging soak credentials." >&2
  exit 1
fi

umask 077
credentials_tmp=$(mktemp "$checkout/run/.env.staging-soak-users.tmp.XXXXXX")
trap 'rm -f -- "$credentials_tmp"' EXIT
worker_a_password=$(openssl rand -hex 24)
worker_b_password=$(openssl rand -hex 24)
manager_password=$(openssl rand -hex 24)
{
  printf 'MITO_STAGING_TEST_USERNAME=manager\n'
  printf 'MITO_STAGING_TEST_PASSWORD=%s\n' "$manager_password"
  printf 'MITO_STAGING_WORKER_A_USERNAME=alice\n'
  printf 'MITO_STAGING_WORKER_A_PASSWORD=%s\n' "$worker_a_password"
  printf 'MITO_STAGING_WORKER_B_USERNAME=carol\n'
  printf 'MITO_STAGING_WORKER_B_PASSWORD=%s\n' "$worker_b_password"
  printf 'MITO_STAGING_SOAK_HOME=/annotator\n'
} >"$credentials_tmp"

export MITO_STAGING_CREDENTIALS_TMP="$credentials_tmp"
set -a
source "$checkout/.env"
source "$credentials_tmp"
set +a
"$checkout/venv/bin/python" "$checkout/backend/manage.py" shell -c '
import os
from django.contrib.auth import get_user_model
from annotation.models import AnnotationTask

User = get_user_model()
expected = (
    (os.environ["MITO_STAGING_WORKER_A_USERNAME"], os.environ["MITO_STAGING_WORKER_A_PASSWORD"]),
    (os.environ["MITO_STAGING_WORKER_B_USERNAME"], os.environ["MITO_STAGING_WORKER_B_PASSWORD"]),
)
resolved = []
for username, password in expected:
    user = User.objects.get(username=username)
    if user.is_staff or user.is_superuser:
        raise SystemExit(f"{username} is privileged; refusing soak setup")
    task = AnnotationTask.objects.filter(assigned_to=user).order_by("pk").first()
    if task is None:
        raise SystemExit(f"no restored task is assigned to {username}")
    user.set_password(password)
    user.save(update_fields=["password"])
    resolved.append((username, task.pk, task.volume_id))
manager = User.objects.get(username=os.environ["MITO_STAGING_TEST_USERNAME"])
if not manager.is_staff:
    raise SystemExit("restored manager is not staff; refusing soak setup")
manager.set_password(os.environ["MITO_STAGING_TEST_PASSWORD"])
manager.save(update_fields=["password"])
with open(os.environ["MITO_STAGING_CREDENTIALS_TMP"], "a", encoding="utf-8") as handle:
    for index, (_username, task_id, volume_id) in enumerate(resolved):
        suffix = "A" if index == 0 else "B"
        handle.write(f"MITO_STAGING_WORKER_{suffix}_VOLUME_ID={volume_id}\n")
        handle.write(f"MITO_STAGING_WORKER_{suffix}_TASK_ID={task_id}\n")
'

chmod 0600 "$credentials_tmp"
mv "$credentials_tmp" "$credentials"
trap - EXIT
echo "Prepared two restored, non-privileged staging users (credentials not displayed)."
