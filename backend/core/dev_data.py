"""Shared helpers for the developer data-management commands.

Seeding creates **accounts plus their default development organization/team** —
one manager, several annotators, and two requesters — and *no* pre-registered
datasets, volumes, or tasks. Any data used during development is registered
manually by developers through the app.

Automated test fixtures are a completely separate concern: the test suite builds
its own throwaway data in temporary directories and never touches these helpers
or the development database.

Nothing here runs automatically — it is only invoked by the management commands
in ``core/management/commands/``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model

from accounts.models import (
    AnnotatorProfile,
    Institution,
    TeamMembership,
    UserProfile,
)
from annotation.models import (
    AnnotationSubmission,
    AnnotationTask,
    HardCase,
    ReviewRecord,
)
from core.choices import TeamRole, UserRole
from projects.models import Project
from volumes.models import Volume

User = get_user_model()

# Standard demo password for every seeded account.
DEMO_PASSWORD = "demo12345"

# The standard development accounts: one manager, four annotators, and two
# requesters (the "customers" the manager's People view is about — without
# them that panel has nothing to show on a fresh database). The manager is a
# superuser, so it survives ``clear_dev_data``. Developers register data
# manually as any of these.
STANDARD_ACCOUNTS = {
    "manager": UserRole.MANAGER,
    "alice": UserRole.ANNOTATOR,
    "bob": UserRole.ANNOTATOR,
    "carol": UserRole.ANNOTATOR,
    "dave": UserRole.ANNOTATOR,
    "requester1": UserRole.REQUESTER,
    "requester2": UserRole.REQUESTER,
}

# Flavour for the People cards so the seeded roster doesn't read as a row of
# identical blanks. Keyed by username; anything missing just stays empty.
STANDARD_PROFILES = {
    "manager": ("Project manager", "Mito Lab", "Reviews submissions and assigns work"),
    "alice": ("Alice N.", "Mito Lab", ""),
    "bob": ("Bob R.", "Mito Lab", ""),
    "carol": ("Carol S.", "Partner Imaging Core", ""),
    "dave": ("Dave K.", "Partner Imaging Core", ""),
    "requester1": ("Dr. Rivera", "Neuroscience Institute", "Prefers instance masks"),
    "requester2": ("Dr. Okafor", "Cell Biology Center", "Batch delivery is fine"),
}

DEV_ORGANIZATION_NAME = "Mito Development"


def _ensure_dev_assignment_team(log=print):
    """Seat the dev workforce on one team and grant all existing projects.

    ``seed_dev`` is the explicit gate: this helper is never called by ordinary
    production project creation. Rerunning the seed repairs grants and selects
    the Default team as the working team for projects that do not have one.
    """
    from accounts.teams import default_team_for

    organization, _ = Institution.objects.get_or_create(
        name=DEV_ORGANIZATION_NAME,
        defaults={"institution_type": "Development"},
    )
    team = default_team_for(organization)
    for username, role in STANDARD_ACCOUNTS.items():
        if role not in {UserRole.MANAGER, UserRole.ANNOTATOR}:
            continue
        user = User.objects.get(username=username)
        UserProfile.objects.filter(user=user).update(institution=organization)
        TeamMembership.objects.update_or_create(
            team=team,
            user=user,
            defaults={
                "role": (
                    TeamRole.MANAGER
                    if role == UserRole.MANAGER
                    else TeamRole.MEMBER
                )
            },
        )
    for project in Project.objects.exclude(teams=team):
        project.teams.add(team)
    Project.objects.filter(working_team__isnull=True).update(working_team=team)
    log(
        "  ensured Default development team for alice, bob, carol, dave "
        "and all existing projects"
    )
    return team


def _ensure_account(
    username: str,
    role: str,
    log=print,
    *,
    safe_mock_login: bool = False,
):
    # Local development historically uses the manager account for Django
    # admin too.  A publicly listed mock account must never inherit that
    # privilege: it is an application manager, not a Django superuser.
    is_manager = role == UserRole.MANAGER and not safe_mock_login
    user, created = User.objects.get_or_create(
        username=username,
        defaults={"is_staff": is_manager, "is_superuser": is_manager},
    )
    if safe_mock_login and settings.MOCK_DEV_LOGIN_PASSWORD:
        user.set_password(settings.MOCK_DEV_LOGIN_PASSWORD)
    elif safe_mock_login:
        user.set_unusable_password()
    else:
        user.set_password(DEMO_PASSWORD)
    user.is_active = True
    user.is_staff = is_manager
    user.is_superuser = is_manager
    user.save()

    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.role = role
    display_name, institution, note = STANDARD_PROFILES.get(username, ("", "", ""))
    profile.display_name = display_name
    profile.institution_name = institution
    profile.contact_note = note
    profile.save()

    if role == UserRole.ANNOTATOR:
        annotator_profile, _ = AnnotatorProfile.objects.get_or_create(
            user=user, defaults={"is_active_annotator": True, "max_active_tasks": 10}
        )
        update_fields = []
        if not annotator_profile.is_active_annotator:
            annotator_profile.is_active_annotator = True
            update_fields.append("is_active_annotator")
        if annotator_profile.max_active_tasks < 1:
            annotator_profile.max_active_tasks = 10
            update_fields.append("max_active_tasks")
        if update_fields:
            annotator_profile.save(update_fields=update_fields)
    log(f"  {'created' if created else 'updated'} {role} '{username}'")
    return user


def seed_standard_data(log=print, *, safe_mock_login: bool = False) -> dict:
    """Create standard dev accounts/team (no project data). Idempotent."""
    log("Seeding standard development accounts…")
    for name, role in STANDARD_ACCOUNTS.items():
        _ensure_account(name, role, log, safe_mock_login=safe_mock_login)
    _ensure_dev_assignment_team(log=log)

    managers = [n for n, r in STANDARD_ACCOUNTS.items() if r == UserRole.MANAGER]
    annotators = [n for n, r in STANDARD_ACCOUNTS.items() if r == UserRole.ANNOTATOR]
    requesters = [n for n, r in STANDARD_ACCOUNTS.items() if r == UserRole.REQUESTER]
    return {
        "managers": managers,
        "annotators": annotators,
        "requesters": requesters,
    }


def _clear_data_root() -> int:
    """Delete everything *inside* ``MITO_DATA_ROOT`` (not the directory
    itself, so its permissions/ownership survive a wipe). Returns how many
    top-level entries were removed.

    Safe to do unconditionally: nothing this app doesn't own ever lives
    under this root — registered-by-reference volumes only ever store an
    absolute (or root-relative-but-external) path *elsewhere*; everything
    actually written inside the root (`volumes/`, `submissions/`, and
    per-project/dataset working label copies) is content this app generated
    and can regenerate. There is no dev/prod distinction to worry about
    either — the caller (`clear_dev_data`) is only ever reachable with
    ``DEBUG`` on (`DevResetView`, `clear_dev_data`/`reset_dev` management
    commands).
    """
    root = Path(settings.MITO_DATA_ROOT)
    if not root.is_dir():
        return 0
    removed = 0
    for entry in root.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
        removed += 1
    return removed


def clear_dev_data(*, keep_users: bool = False, log=print) -> dict:
    """Delete development data. Superusers are always preserved.

    Removes projects, volumes, tasks, submissions, reviews, and institutions,
    **and everything the app itself ever wrote under ``MITO_DATA_ROOT``** —
    uploaded image/label/submission files (Django `FileField`s) *and* the
    in-app editor's working label copies (`annotation.label_paths`, written
    directly by path, not through a `FileField`, so they need this — a
    per-`FileField` `.delete()` loop alone would miss them entirely; that was
    a real bug, see `progress/history/17-fix-dev-reset-orphaned-files.md`).
    Registered-*by-reference* volumes only store a path string pointing
    outside `MITO_DATA_ROOT` (someone else's HPC data) — those are never
    touched, only the DB row referencing them. Non-superuser accounts are
    removed too unless ``keep_users`` is set. Returns a dict of deleted counts.
    """
    log("Clearing development data…")

    counts = {
        "hard_cases": HardCase.objects.all().delete()[0],
        "reviews": ReviewRecord.objects.all().delete()[0],
        "submissions": AnnotationSubmission.objects.all().delete()[0],
        "tasks": AnnotationTask.objects.all().delete()[0],
        "volumes": Volume.objects.all().delete()[0],
        "projects": Project.objects.all().delete()[0],
        "institutions": Institution.objects.all().delete()[0],
    }

    files_removed = _clear_data_root()
    log(f"  cleared {files_removed} item(s) under MITO_DATA_ROOT")

    # The Django dev server is a long-running process — deleting working
    # label files out from under it without also dropping slice_io's caches
    # leaves a stale *writable* memmap handle open (keyed only by path, not
    # mtime/inode — unlike the read-side volume cache). A later request for
    # the same path (e.g. a volume re-registered after reset landing on the
    # same id, which SQLite rowid reuse makes possible — see
    # `annotation/test_tracking.py`'s setUp comment on the same issue) would
    # then silently read/write the orphaned old file instead of the new one.
    # `track_task_fork`/`_save_label_volume` already clear these caches after
    # a full label rewrite for the same reason; a full data reset is at least
    # as disruptive to what's on disk.
    from annotation.visualization import slice_io

    slice_io.clear_caches()

    if not keep_users:
        # Preserve superusers so admin access survives a wipe.
        qs = User.objects.filter(is_superuser=False)
        counts["users"] = qs.count()
        qs.delete()
    else:
        counts["users"] = 0

    for key, value in counts.items():
        log(f"  deleted {value} {key}")
    return counts


def data_summary() -> dict:
    """Current row counts, for the ``dev_status`` command."""
    return {
        "users": User.objects.count(),
        "superusers": User.objects.filter(is_superuser=True).count(),
        "requesters": UserProfile.objects.filter(role=UserRole.REQUESTER).count(),
        "annotators": UserProfile.objects.filter(role=UserRole.ANNOTATOR).count(),
        "projects": Project.objects.count(),
        "volumes": Volume.objects.count(),
        "tasks": AnnotationTask.objects.count(),
        "submissions": AnnotationSubmission.objects.count(),
        "reviews": ReviewRecord.objects.count(),
        "hard_cases": HardCase.objects.count(),
    }
