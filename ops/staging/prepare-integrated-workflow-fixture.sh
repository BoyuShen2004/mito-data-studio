#!/usr/bin/env bash
set -euo pipefail

# Add an isolated, clearly named workflow fixture to the restored-production
# staging database. This never copies or mutates source/reference bytes and it
# refuses to run without the staging identity pins.
staging_root=${MITO_STAGING_ROOT:-/home/weidf/shenb/mito-data-agent-staging-v1.1.0}
staging_user=${MITO_STAGING_USER:-mito-staging-v11}
staging_home=${MITO_STAGING_HOME:-/home/mito-staging-v11}
soak_credentials=${MITO_SOAK_CREDENTIALS_FILE:-$staging_root/run/.env.staging-soak-users}
workflow_credentials=${MITO_WORKFLOW_CREDENTIALS_FILE:-$staging_root/run/.env.staging-workflow-users}
fixture_run=${MITO_WORKFLOW_FIXTURE_RUN:-primary}

[[ "$fixture_run" =~ ^[A-Za-z0-9_-]+$ ]]

test -f "$staging_root/.env"
sudo test -f "$soak_credentials"
sudo test ! -e "$workflow_credentials"
test "$(sudo awk -F= '$1 == "MITO_EXPECTED_DB_NAME" {print $2}' "$staging_root/.env")" = \
  "mito_staging_v1_1_0"
test "$(sudo awk -F= '$1 == "MITO_EXPECTED_DATA_ROOT" {print $2}' "$staging_root/.env")" = \
  "/home/weidf/shenb/mito-data-agent-staging-data-v1.1.0"

sudo -u "$staging_user" env HOME="$staging_home" \
  STAGING_ROOT="$staging_root" \
  SOAK_CREDENTIALS="$soak_credentials" \
  WORKFLOW_CREDENTIALS="$workflow_credentials" \
  WORKFLOW_FIXTURE_RUN="$fixture_run" \
  bash -c '
    set -a
    source "$STAGING_ROOT/.env"
    source "$SOAK_CREDENTIALS"
    set +a
    export DJANGO_SETTINGS_MODULE=config.settings
    cd "$STAGING_ROOT/backend"
    ../venv/bin/python - <<"PY"
import os
import secrets
from pathlib import Path

import django

django.setup()

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import AnnotatorProfile, Institution, UserProfile
from accounts.teams import add_team_member, grant_project_team
from annotation.models import AnnotationTask
from core.choices import TaskStatus, TeamRole, UserRole


manager = User.objects.get(username=os.environ["MITO_STAGING_TEST_USERNAME"])
manager_password = os.environ["MITO_STAGING_TEST_PASSWORD"]
annotator_password = secrets.token_urlsafe(32)
fixture_run = os.environ["WORKFLOW_FIXTURE_RUN"]
marker = f"release-v1.1-integrated-workflow-fixture-{fixture_run}"
annotator_username = f"release_assignee_v11_{fixture_run}"

with transaction.atomic():
    annotator, _ = User.objects.get_or_create(username=annotator_username)
    annotator.set_password(annotator_password)
    annotator.is_active = True
    annotator.save(update_fields=["password", "is_active"])
    UserProfile.objects.update_or_create(
        user=annotator,
        defaults={"role": UserRole.ANNOTATOR, "display_name": "Release Assignee"},
    )
    AnnotatorProfile.objects.update_or_create(
        user=annotator,
        defaults={"is_active_annotator": True, "max_active_tasks": 5},
    )

    organization, _ = Institution.objects.get_or_create(
        name="Release Verification Organization"
    )
    team, _ = organization.teams.get_or_create(
        name="Release Verification Team",
        defaults={"description": "v1.1 staging-only integrated workflow fixture"},
    )
    UserProfile.objects.filter(user__in=[manager, annotator]).update(
        institution=organization
    )
    add_team_member(team, manager, role=TeamRole.MANAGER, actor=manager)
    add_team_member(team, annotator, role=TeamRole.MEMBER, actor=manager)

    source = (
        AnnotationTask.objects.select_related("project", "volume")
        .filter(project__manager_reviewed=True)
        .order_by("id")
        .first()
    )
    if source is None:
        raise RuntimeError("restored staging has no approved source task")
    grant_project_team(source.project, team, actor=manager)
    def create_assigned_task(suffix, priority):
        assigned_at = timezone.now()
        task, created = AnnotationTask.objects.get_or_create(
            project=source.project,
            instructions=f"{marker}-{suffix}",
            defaults={
                "volume": source.volume,
                "z_start": source.z_start,
                "z_end": source.z_end,
                "y_start": source.y_start,
                "y_end": source.y_end,
                "x_start": source.x_start,
                "x_end": source.x_end,
                "task_type": source.task_type,
                "assigned_to": annotator,
                "assigned_at": assigned_at,
                "status": TaskStatus.ASSIGNED,
                "priority": priority,
                "difficulty": source.difficulty,
            },
        )
        if not created and (
            task.status != TaskStatus.ASSIGNED
            or task.assigned_to_id != annotator.id
        ):
            raise RuntimeError(
                f"existing partial fixture task {suffix} is not pristine; refusing repair"
            )
        return task

    assigned_task = create_assigned_task("gate", 1_000_000)
    soak_assigned_task = create_assigned_task("soak", 999_999)

    region_task = (
        AnnotationTask.objects.filter(
            Q(volume__region_mask_path__gt="") | Q(volume__region_mask_file__gt="")
        )
        .order_by("id")
        .first()
    )
    if region_task is None:
        raise RuntimeError("restored staging has no representative region-mask task")

content = "\n".join(
    [
        f"MITO_STAGING_TEST_USERNAME={manager.username}",
        f"MITO_STAGING_TEST_PASSWORD={manager_password}",
        f"MITO_STAGING_ASSIGNEE_USERNAME={annotator_username}",
        f"MITO_STAGING_ASSIGNEE_PASSWORD={annotator_password}",
        f"MITO_STAGING_ASSIGNED_TASK_ID={assigned_task.id}",
        f"MITO_STAGING_SOAK_ASSIGNED_TASK_ID={soak_assigned_task.id}",
        f"MITO_STAGING_WORKFLOW_PROJECT_ID={source.project_id}",
        f"MITO_STAGING_REGION_TASK_ID={region_task.id}",
        "MITO_STAGING_WORKFLOW_ORGANIZATION=Release\\ Verification\\ Organization",
        "MITO_STAGING_WORKFLOW_TEAM=Release\\ Verification\\ Team",
        "",
    ]
)
target = Path(os.environ["WORKFLOW_CREDENTIALS"])
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    handle.write(content)
PY
  '

sudo test -s "$workflow_credentials"
echo "Prepared protected integrated-workflow staging fixture (credentials not displayed)."
