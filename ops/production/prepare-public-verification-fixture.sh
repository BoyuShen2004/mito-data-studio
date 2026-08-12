#!/usr/bin/env bash
set -euo pipefail

# Create a small, isolated production verification project after the final DB
# restore. Its immutable source/reference files live in a systemd-read-only
# root; browser Save/autosave writes only to the new v1.1 data root. This makes
# public write-target proof repeatable without touching a user's real volume.
checkout=${MITO_PRODUCTION_ROOT:-/home/weidf/shenb/mito-data-studio-production-v1.1.0}
data_root=${MITO_PRODUCTION_DATA_ROOT:-/home/weidf/shenb/mito-data-studio-production-data-v1.1.0}
source_root=${MITO_VALIDATION_SOURCE_ROOT:-/home/weidf/shenb/mito-data-studio-validation-source-v1.1.0}
service_user=${MITO_PRODUCTION_USER:-mito-production-v11}
service_group=${MITO_PRODUCTION_GROUP:-mito-production-v11}
credentials=${MITO_PUBLIC_VERIFICATION_CREDENTIALS:-$checkout/run/.env.public-verification}

test -f "$checkout/.env"
test -x "$checkout/venv/bin/python"
test -d "$data_root"
sudo test ! -e "$credentials"
test "$(sudo awk -F= '$1 == "MITO_EXPECTED_DB_NAME" {print $2}' "$checkout/.env")" = \
  "mito_production_v1_1_0"
test "$(sudo awk -F= '$1 == "MITO_EXPECTED_DATA_ROOT" {print $2}' "$checkout/.env")" = \
  "$data_root"

if ! sudo test -e "$source_root"; then
  sudo install -d -o root -g "$service_group" -m 0750 "$source_root"
  sudo env VALIDATION_SOURCE_ROOT="$source_root" "$checkout/venv/bin/python" - <<'PY'
import os
from pathlib import Path

import numpy as np
import tifffile

root = Path(os.environ["VALIDATION_SOURCE_ROOT"])
z, y, x = np.indices((8, 96, 96))
image = ((x * 2 + y * 3 + z * 17) % 256).astype(np.uint8)
labels = np.zeros((8, 96, 96), dtype=np.uint16)
labels[:, 20:44, 20:44] = 1
labels[2:7, 52:78, 50:80] = 2
region = np.zeros_like(labels)
region[:, 8:88, 8:88] = 1
tifffile.imwrite(root / "source.tif", image, metadata={"axes": "ZYX"})
tifffile.imwrite(root / "reference-mask.tif", labels, metadata={"axes": "ZYX"})
tifffile.imwrite(root / "region-mask.tif", region, metadata={"axes": "ZYX"})
PY
fi

# The directory deliberately becomes inaccessible to the invoking operator
# once it is owned by root:service_group.  Avoid caller-expanded globs so an
# interruption after TIFF generation can be resumed safely.
sudo test -d "$source_root"
test "$(sudo find "$source_root" -mindepth 1 -maxdepth 1 -type f -name '*.tif' -printf '%f\n' | sort)" = \
  $'reference-mask.tif\nregion-mask.tif\nsource.tif'
test "$(sudo sha256sum \
  "$source_root/reference-mask.tif" \
  "$source_root/region-mask.tif" \
  "$source_root/source.tif" | awk '{print $1}')" = \
  $'62831538e60db8c8ff62ebc54e923b33eb21d030035f7ee2811bcdde2670332c\naf367ae0314194479abed0d358c540b68fbb34f6d69945f860b4f77adf8c82d3\na1dfd958d202b77e2ae3bdb0099d290b3ea4becfccce11af2bf13995e7cfdc69'
sudo find "$source_root" -mindepth 1 -maxdepth 1 -type f -name '*.tif' \
  -exec chown root:"$service_group" {} + \
  -exec chmod 0440 {} +
sudo chmod 0550 "$source_root"
sudo find "$source_root" -mindepth 1 -maxdepth 1 -type f -name '*.tif' \
  -exec sha256sum {} + | sort -k2 | \
  sudo tee "$checkout/run/public-verification-source.sha256" >/dev/null
sudo chown "$service_user":"$service_group" "$checkout/run/public-verification-source.sha256"
sudo chmod 0440 "$checkout/run/public-verification-source.sha256"

sudo -u "$service_user" env HOME="/home/$service_user" \
  PRODUCTION_ROOT="$checkout" \
  PRODUCTION_DATA_ROOT="$data_root" \
  VALIDATION_SOURCE_ROOT="$source_root" \
  PUBLIC_VERIFICATION_CREDENTIALS="$credentials" \
  bash -c '
    set -a
    source "$PRODUCTION_ROOT/.env"
    set +a
    export DJANGO_SETTINGS_MODULE=config.settings
    cd "$PRODUCTION_ROOT/backend"
    ../venv/bin/python - <<"PY"
import os
import secrets
from pathlib import Path

import django

django.setup()

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from accounts.models import AnnotatorProfile, Institution, UserProfile
from accounts.teams import add_team_member, grant_project_team
from annotation.models import AnnotationTask
from core.choices import (
    LabelType,
    ProjectStatus,
    TaskStatus,
    TaskType as TaskKind,
    TeamRole,
    UserRole,
    VolumeStatus,
)
from projects.models import Dataset, Project
from volumes.models import Volume


manager_password = secrets.token_urlsafe(32)
source_root = Path(os.environ["VALIDATION_SOURCE_ROOT"])

with transaction.atomic():
    manager, _ = User.objects.get_or_create(username="release_manager_v11")
    manager.set_password(manager_password)
    manager.is_active = True
    manager.is_staff = True
    manager.save(update_fields=["password", "is_active", "is_staff"])
    UserProfile.objects.update_or_create(
        user=manager,
        defaults={"role": UserRole.MANAGER, "display_name": "Release Manager"},
    )

    def create_annotator(username, display_name):
        password = secrets.token_urlsafe(32)
        user, _ = User.objects.get_or_create(username=username)
        user.set_password(password)
        user.is_active = True
        user.is_staff = False
        user.is_superuser = False
        user.save(update_fields=["password", "is_active", "is_staff", "is_superuser"])
        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": UserRole.ANNOTATOR, "display_name": display_name},
        )
        AnnotatorProfile.objects.update_or_create(
            user=user,
            defaults={"is_active_annotator": True, "max_active_tasks": 5},
        )
        return user, password

    private_annotator, private_annotator_password = create_annotator(
        "release_private_annotator_v11", "Release Private Annotator"
    )
    public_annotator, public_annotator_password = create_annotator(
        "release_public_annotator_v11", "Release Public Annotator"
    )

    organization = Institution.objects.create(name="Release Operations v1.1.0")
    UserProfile.objects.filter(
        user__in=[manager, private_annotator, public_annotator]
    ).update(
        institution=organization
    )
    team = organization.teams.create(
        name="Release Verification",
        description="Retained v1.1.0 public workflow verification team",
    )
    add_team_member(team, manager, role=TeamRole.MANAGER, actor=manager)
    for annotator in (private_annotator, public_annotator):
        add_team_member(team, annotator, role=TeamRole.MEMBER, actor=manager)

    project = Project.objects.create(
        title="Release Verification v1.1.0",
        dataset="release-verification-v1.1.0",
        institution=organization,
        description="Retained synthetic data for public release verification",
        status=ProjectStatus.ACTIVE,
        created_by=manager,
        manager_reviewed=True,
        reviewed_by=manager,
        reviewed_at=timezone.now(),
        priority=1_000_000,
    )
    grant_project_team(project, team, actor=manager)
    dataset = Dataset.objects.create(
        project=project,
        name="release-verification-v1.1.0",
        image_directory=str(source_root),
        mask_directory=str(source_root),
        region_mask_directory=str(source_root),
        metadata={"purpose": "v1.1.0 public cutover verification"},
    )
    def create_verification_volume(scope):
        return Volume.objects.create(
            project=project,
            dataset=dataset,
            name=f"release-verification-{scope}-volume",
            image_path=str(source_root / "source.tif"),
            label_path=str(source_root / "reference-mask.tif"),
            region_mask_path=str(source_root / "region-mask.tif"),
            label_type=LabelType.PARTIAL,
            shape_z=8,
            shape_y=96,
            shape_x=96,
            status=VolumeStatus.IN_ANNOTATION,
            metadata={"purpose": f"{scope} cutover write-target proof"},
        )

    private_volume = create_verification_volume("private")
    public_volume = create_verification_volume("public")
    def create_verification_task(scope, priority, volume, annotator):
        assigned_at = timezone.now()
        task = AnnotationTask.objects.create(
            project=project,
            volume=volume,
            z_start=0,
            z_end=7,
            y_start=0,
            y_end=96,
            x_start=0,
            x_end=96,
            task_type=TaskKind.MANUAL_ANNOTATION,
            assigned_to=annotator,
            assigned_at=assigned_at,
            status=TaskStatus.ASSIGNED,
            priority=priority,
            difficulty=1,
            instructions=f"Retained v1.1.0 {scope} verification task",
        )
        return task

    private_task = create_verification_task(
        "private", 1_000_000, private_volume, private_annotator
    )
    public_task = create_verification_task(
        "public", 999_999, public_volume, public_annotator
    )

content = "\n".join(
    [
        "MITO_PUBLIC_MANAGER_USERNAME=release_manager_v11",
        f"MITO_PUBLIC_MANAGER_PASSWORD={manager_password}",
        "MITO_PUBLIC_ANNOTATOR_USERNAME=release_private_annotator_v11",
        f"MITO_PUBLIC_ANNOTATOR_PASSWORD={private_annotator_password}",
        "MITO_PUBLIC_PRIVATE_ANNOTATOR_USERNAME=release_private_annotator_v11",
        f"MITO_PUBLIC_PRIVATE_ANNOTATOR_PASSWORD={private_annotator_password}",
        "MITO_PUBLIC_PUBLIC_ANNOTATOR_USERNAME=release_public_annotator_v11",
        f"MITO_PUBLIC_PUBLIC_ANNOTATOR_PASSWORD={public_annotator_password}",
        f"MITO_PUBLIC_PROJECT_ID={project.id}",
        f"MITO_PUBLIC_VOLUME_ID={private_volume.id}",
        f"MITO_PUBLIC_PRIVATE_VOLUME_ID={private_volume.id}",
        f"MITO_PUBLIC_PUBLIC_VOLUME_ID={public_volume.id}",
        f"MITO_PUBLIC_TASK_ID={private_task.id}",
        f"MITO_PUBLIC_PRIVATE_TASK_ID={private_task.id}",
        f"MITO_PUBLIC_PUBLIC_TASK_ID={public_task.id}",
        "",
    ]
)
target = Path(os.environ["PUBLIC_VERIFICATION_CREDENTIALS"])
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    handle.write(content)
PY
  '

sudo test -s "$credentials"
echo "Prepared isolated public verification fixture (credentials not displayed)."
