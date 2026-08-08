"""Guarded fresh-application reset shared by CLI and the superuser API."""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from accounts.models import AuditEvent, Institution, Team, TeamMembership
from annotation.models import (
    AnnotationOperation,
    AnnotationSubmission,
    AnnotationTask,
    HardCase,
    ReviewRecord,
    SchedulerDecision,
    WorkSession,
)
from core.deployment import identity
from core.choices import TERMINAL_JOB_STATUSES
from core.models import ApplicationResetRecord, ResetConfirmation
from processing.models import ProcessingJob
from projects.models import Dataset, Project, ProjectMembership, PublicShare
from volumes.models import Volume

CONFIRM_PHRASE = "CLEAR ALL APPLICATION DATA"
DEVELOPMENT_CONFIRM_PHRASE = "CLEAR ALL DEVELOPMENT DATA"


class ResetRefused(RuntimeError):
    pass


def preserved_usernames() -> tuple[str, ...]:
    """Accounts are configuration; reset their work, not their identities."""
    return tuple(
        dict.fromkeys(
            (settings.MITO_RESET_ADMIN_USERNAME, *settings.MOCK_DEV_LOGIN_ACCOUNTS)
        )
    )


def _resolved(path: str, root: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve(strict=False)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def storage_manifest() -> list[dict[str, object]]:
    root = Path(settings.MITO_DATA_ROOT).resolve()
    rows: list[dict[str, object]] = []
    for volume in Volume.objects.select_related("project", "dataset").order_by("id"):
        fields = (
            ("image_path", volume.image_path, "external source image"),
            ("label_path", volume.label_path, "external official/reference label"),
            ("region_mask_path", volume.region_mask_path, "external official/reference label"),
            ("image_file", volume.image_file.name if volume.image_file else "", "app-owned upload"),
            ("label_file", volume.label_file.name if volume.label_file else "", "app-owned upload"),
            ("region_mask_file", volume.region_mask_file.name if volume.region_mask_file else "", "app-owned upload"),
        )
        for field, value, default_class in fields:
            if not value:
                continue
            resolved = _resolved(value, root)
            classification = "app-owned upload" if _inside(resolved, root) else default_class
            rows.append({
                "volume_id": volume.id,
                "field": field,
                "stored_path": value,
                "resolved_path": str(resolved),
                "classification": classification,
                "exists": resolved.exists(),
                "symlink": Path(value).is_symlink() if Path(value).is_absolute() else False,
                "action": "delete" if classification.startswith("app-owned") else "unregister only",
            })
    if root.is_dir():
        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            is_link = entry.is_symlink()
            rows.append({
                "volume_id": None,
                "field": "data_root_entry",
                "stored_path": entry.name,
                "resolved_path": str(entry.absolute() if is_link else entry.resolve(strict=False)),
                "link_target": str(entry.resolve(strict=False)) if is_link else None,
                "classification": "app-owned link" if is_link else "app-owned generated data",
                "exists": True,
                "symlink": is_link,
                "action": "delete link only" if is_link else "delete",
            })
    return rows


def row_summary() -> dict[str, int]:
    User = get_user_model()
    return {
        "projects": Project.objects.count(),
        "project_memberships": ProjectMembership.objects.count(),
        "project_team_grants": Project.teams.through.objects.count(),
        "datasets": Dataset.objects.count(),
        "volumes": Volume.objects.count(),
        "tasks": AnnotationTask.objects.count(),
        "submissions": AnnotationSubmission.objects.count(),
        "reviews": ReviewRecord.objects.count(),
        "hard_cases": HardCase.objects.count(),
        "annotation_operations": AnnotationOperation.objects.count(),
        "work_sessions": WorkSession.objects.count(),
        "scheduler_decisions": SchedulerDecision.objects.count(),
        "public_shares": PublicShare.objects.count(),
        "processing_jobs": ProcessingJob.objects.count(),
        "organizations": Institution.objects.count(),
        "teams": Team.objects.count(),
        "memberships": TeamMembership.objects.count(),
        "audit_events": AuditEvent.objects.count(),
        "admin_log_entries": LogEntry.objects.count(),
        "users": User.objects.count(),
        "users_to_delete": User.objects.exclude(
            username__in=preserved_usernames()
        ).count(),
        "users_to_preserve": User.objects.filter(
            username__in=preserved_usernames()
        ).count(),
    }


def backup_status() -> dict[str, object]:
    raw = settings.MITO_RESET_BACKUP_MARKER
    if not raw:
        return {"valid": False, "reason": "MITO_RESET_BACKUP_MARKER is not configured"}
    marker = Path(raw).resolve(strict=False)
    try:
        payload = json.loads(marker.read_text())
        age = timezone.now().timestamp() - marker.stat().st_mtime
    except (OSError, ValueError, TypeError) as exc:
        return {"valid": False, "reason": f"backup marker cannot be verified: {exc}"}
    required = ("database_dump", "data_archive", "database_sha256", "data_sha256", "verified_at")
    if any(not payload.get(key) for key in required):
        return {"valid": False, "reason": "backup marker is incomplete"}
    if age < 0 or age > settings.MITO_RESET_BACKUP_MAX_AGE_SECONDS:
        return {"valid": False, "reason": "backup verification is stale"}
    for key in ("database_dump", "data_archive"):
        path = Path(payload[key])
        if not path.is_file() or path.stat().st_size == 0:
            return {"valid": False, "reason": f"backup artifact missing: {key}"}
    return {"valid": True, "marker": str(marker), "verified_at": payload["verified_at"]}


def assert_reset_environment() -> dict[str, object]:
    info = identity()
    expected = {
        "checkout": __import__("os").environ.get("MITO_EXPECTED_CHECKOUT"),
        "data_root": __import__("os").environ.get("MITO_EXPECTED_DATA_ROOT"),
        "database": __import__("os").environ.get("MITO_EXPECTED_DB_NAME"),
    }
    actual_db = info["database"]["name"]
    if not all(expected.values()):
        raise ResetRefused("recognized production identity is not fully pinned")
    if str(info["checkout"]) != expected["checkout"] or str(info["data_root"]) != expected["data_root"] or str(actual_db) != expected["database"]:
        raise ResetRefused("active deployment identity does not match MITO_EXPECTED_*")
    if not settings.MITO_MAINTENANCE_MODE:
        raise ResetRefused("maintenance/write-freeze mode is inactive")
    backup = backup_status()
    if not backup["valid"]:
        raise ResetRefused(str(backup["reason"]))
    return {"identity": info, "backup": backup}


def issue_confirmation(user, phrase: str) -> str:
    if not user.is_superuser or phrase != CONFIRM_PHRASE:
        raise ResetRefused("superuser and exact confirmation phrase required")
    context = assert_reset_environment()
    cutoff = timezone.now() - timedelta(seconds=60)
    if ResetConfirmation.objects.filter(requested_by=user, created_at__gte=cutoff).exists():
        raise ResetRefused("confirmation requests are rate-limited")
    raw = secrets.token_urlsafe(32)
    ResetConfirmation.objects.create(
        requested_by=user,
        token_digest=hashlib.sha256(raw.encode()).hexdigest(),
        deployment_fingerprint=context["identity"]["fingerprint"],
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    return raw


def _stage_data_root() -> tuple[Path, list[str]]:
    root = Path(settings.MITO_DATA_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    trash = root / f".reset-trash-{timezone.now().strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"
    trash.mkdir(mode=0o700)
    moved: list[str] = []
    for entry in list(root.iterdir()):
        if entry == trash:
            continue
        # Moving a symlink moves only the link object; its external target is
        # never followed. Skipping links left visible registered entries after
        # an otherwise successful reset.
        entry.rename(trash / entry.name)
        moved.append(entry.name)
    return trash, moved


def _quiesce_processing_jobs() -> list[int]:
    """Cancel every job that could recreate files after the wipe starts."""
    from processing.services import cancel_job

    cancelled: list[int] = []
    with transaction.atomic():
        jobs = list(
            ProcessingJob.objects.select_for_update()
            .exclude(status__in=TERMINAL_JOB_STATUSES)
            .order_by("id")
        )
        for job in jobs:
            cancel_job(job)
            job.refresh_from_db()
            if job.status not in TERMINAL_JOB_STATUSES:
                raise ResetRefused(
                    f"processing job {job.pk} could not be stopped; reset aborted"
                )
            cancelled.append(job.pk)
    return cancelled


def _execute_reset_core(context: dict[str, object]) -> dict[str, object]:
    """Perform the complete wipe after the caller has enforced its gate."""
    before = row_summary()
    files = storage_manifest()
    cancelled_jobs = _quiesce_processing_jobs()
    trash, moved = _stage_data_root()
    User = get_user_model()
    try:
        with transaction.atomic():
            ProcessingJob.objects.all().delete()
            PublicShare.objects.all().delete()
            AnnotationOperation.objects.all().delete()
            WorkSession.objects.all().delete()
            SchedulerDecision.objects.all().delete()
            HardCase.objects.all().delete()
            ReviewRecord.objects.all().delete()
            AnnotationSubmission.objects.all().delete()
            AnnotationTask.objects.all().delete()
            ProjectMembership.objects.all().delete()
            Volume.objects.all().delete()
            Dataset.objects.all().delete()
            Project.objects.all().delete()
            TeamMembership.objects.all().delete()
            Team.objects.all().delete()
            Institution.objects.all().delete()
            AuditEvent.objects.all().delete()
            LogEntry.objects.all().delete()
            Session.objects.all().delete()
            User.objects.exclude(username__in=preserved_usernames()).delete()
            admin = User.objects.get(username=settings.MITO_RESET_ADMIN_USERNAME, is_superuser=True)
            # Invalidate every retained account's old API session. The login
            # selector will issue a fresh token on its next use.
            from rest_framework.authtoken.models import Token

            Token.objects.filter(user__username__in=preserved_usernames()).delete()
            record = ApplicationResetRecord.objects.create(
                actor=admin,
                deployment_fingerprint=context["identity"]["fingerprint"],
                backup_marker=context["backup"]["marker"],
                manifest={
                    "before": before,
                    "storage": files,
                    "moved": moved,
                    "cancelled_processing_jobs": cancelled_jobs,
                },
            )
            AuditEvent.objects.create(
                actor=admin, verb="application.reset", target_type="deployment",
                target_id=context["identity"]["fingerprint"],
                metadata={"reset_record_id": record.id, "before": before},
            )
    except Exception:
        for entry in list(trash.iterdir()):
            entry.rename(Path(settings.MITO_DATA_ROOT) / entry.name)
        trash.rmdir()
        raise
    from annotation.visualization import slice_io
    slice_io.clear_caches()
    shutil.rmtree(trash)
    return {
        "before": before,
        "after": row_summary(),
        "files_removed": moved,
        "cancelled_processing_jobs": cancelled_jobs,
        "storage_manifest": files,
    }


def execute_reset(user, raw_token: str, phrase: str) -> dict[str, object]:
    """Production reset: superuser, backup, maintenance and one-use token."""
    if not user.is_superuser or phrase != CONFIRM_PHRASE:
        raise ResetRefused("superuser and exact confirmation phrase required")
    context = assert_reset_environment()
    digest = hashlib.sha256(raw_token.encode()).hexdigest()
    with transaction.atomic():
        confirmation = ResetConfirmation.objects.select_for_update().filter(
            requested_by=user, token_digest=digest, used_at__isnull=True,
            expires_at__gt=timezone.now(),
            deployment_fingerprint=context["identity"]["fingerprint"],
        ).first()
        if confirmation is None:
            raise ResetRefused("confirmation token is invalid, expired, used, or for another deployment")
        confirmation.used_at = timezone.now()
        confirmation.save(update_fields=["used_at"])
    return _execute_reset_core(context)


def execute_development_reset(phrase: str) -> dict[str, object]:
    """Login-page reset for explicitly configured disposable deployments.

    This deliberately skips the production backup/maintenance/password gates,
    but only exists when both the development-account and reset switches are
    enabled. The API additionally supplies CSRF protection and the UI requires
    an explicit browser confirmation.
    """
    if not (
        settings.ENABLE_MOCK_DEV_LOGIN
        and settings.MITO_ALLOW_DEV_RESET
        and settings.MOCK_DEV_LOGIN_ACCOUNTS
    ):
        raise ResetRefused("development reset is disabled")
    if phrase != DEVELOPMENT_CONFIRM_PHRASE:
        raise ResetRefused("explicit development reset confirmation required")
    User = get_user_model()
    if not User.objects.filter(
        username=settings.MITO_RESET_ADMIN_USERNAME, is_superuser=True
    ).exists():
        raise ResetRefused("configured reset administrator is unavailable")
    info = identity()
    return _execute_reset_core({
        "identity": info,
        "backup": {"marker": "development reset (backup not required)"},
    })
