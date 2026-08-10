"""Team membership and team-aware access predicates.

Everything that *changes* an access decision is gated on
``settings.FEATURE_TEAMS``. With the flag off these predicates still work and
are still tested, but the callers in ``annotation.services`` fall through to
the legacy rules — so the schema can land and be backfilled well before any
behaviour moves. See ``docs/webknossos-transformation/16-target-domain-model.md``.

Role vocabulary, deliberately kept distinct:

* **org role** (``UserProfile.role``) — manager / annotator / requester
* **team role** (``TeamMembership.role``) — member / team manager

A user may be an annotator org-wide and a manager of one team. Org managers and
superusers outrank both.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction

from core.choices import AuditVerb, TeamRole

from .audit import record_audit
from .models import Team, TeamMembership
from .roles import is_manager


def teams_enabled() -> bool:
    """Is team-based access control switched on?"""
    return bool(getattr(settings, "FEATURE_TEAMS", False))


# --- membership ------------------------------------------------------------

def user_teams(user):
    """Every team ``user`` belongs to (empty for anonymous users)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Team.objects.none()
    return Team.objects.filter(memberships__user=user).distinct()


def user_team_ids(user) -> set[int]:
    return set(user_teams(user).values_list("id", flat=True))


def managed_teams(user):
    """Teams ``user`` manages. Org managers/superusers manage every team."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Team.objects.none()
    if is_manager(user):
        return Team.objects.all()
    return Team.objects.filter(
        memberships__user=user, memberships__role=TeamRole.MANAGER
    ).distinct()


def is_team_member(user, team) -> bool:
    if team is None or user is None or not getattr(user, "is_authenticated", False):
        return False
    return TeamMembership.objects.filter(team=team, user=user).exists()


def is_team_manager(user, team) -> bool:
    """Does ``user`` manage ``team``? Org managers always do."""
    if team is None or user is None or not getattr(user, "is_authenticated", False):
        return False
    if is_manager(user):
        return True
    return TeamMembership.objects.filter(
        team=team, user=user, role=TeamRole.MANAGER
    ).exists()


@transaction.atomic
def add_team_member(team, user, *, role=TeamRole.MEMBER, actor=None) -> TeamMembership:
    """Add (or re-role) ``user`` on ``team``, recording an audit event."""
    membership, created = TeamMembership.objects.get_or_create(
        team=team, user=user, defaults={"role": role}
    )
    if created:
        record_audit(
            actor, AuditVerb.TEAM_MEMBER_ADDED, team,
            user_id=user.id, username=user.get_username(), role=str(role),
        )
        _sync_working_project_access(team, user, actor=actor)
        return membership

    if membership.role != role:
        previous = membership.role
        membership.role = role
        membership.save(update_fields=["role"])
        record_audit(
            actor, AuditVerb.TEAM_ROLE_CHANGED, team,
            user_id=user.id, username=user.get_username(),
            previous_role=str(previous), role=str(role),
        )
    _sync_working_project_access(team, user, actor=actor)
    return membership


def _sync_working_project_access(team, user, *, actor=None) -> None:
    """Mirror working-team eligibility into explicit project browse access."""
    from projects.models import ProjectMembership

    for project in team.working_projects.all():
        ProjectMembership.objects.get_or_create(
            project=project, user=user, defaults={"added_by": actor}
        )


@transaction.atomic
def ensure_project_assignee_eligible(project, user, *, actor=None):
    """One roster write used by Project Access and assignment-team UI.

    A project without a working team receives its organisation's default team;
    the person is then both a team member (assignable) and an explicit project
    member (browse/Hard Cases). No third roster is introduced.
    """
    from .models import Institution
    from projects.models import ProjectMembership

    team = project.working_team
    if team is None:
        organization = getattr(project, "institution", None)
        if organization is None:
            organization = getattr(getattr(user, "profile", None), "institution", None)
        if organization is None:
            organization = getattr(getattr(actor, "profile", None), "institution", None)
        if organization is None:
            organization, _ = Institution.objects.get_or_create(name="Mito Data Agent")
        team = default_team_for(organization)
        set_project_working_team(project, team, actor=actor)
    membership = add_team_member(team, user, actor=actor)
    access, _ = ProjectMembership.objects.get_or_create(
        project=project, user=user, defaults={"added_by": actor}
    )
    return membership, access


@transaction.atomic
def remove_team_member(team, user, *, actor=None) -> bool:
    """Remove ``user`` from ``team``. Returns whether anything was removed."""
    from annotation.services import withdraw_project_assignments

    for project in team.working_projects.all():
        withdraw_project_assignments(
            project,
            team_name=team.name,
            only_annotator_ids=[user.id],
            reason="Removed from the project's working team",
        )
    deleted, _ = TeamMembership.objects.filter(team=team, user=user).delete()
    if deleted:
        record_audit(
            actor, AuditVerb.TEAM_MEMBER_REMOVED, team,
            user_id=user.id, username=user.get_username(),
        )
    return bool(deleted)


def default_team_for(organization, *, create=True):
    """The organisation's default team, created on first use when asked."""
    if organization is None:
        return None
    team = Team.objects.filter(organization=organization, is_default=True).first()
    if team or not create:
        return team
    return Team.objects.create(
        organization=organization, name="Default", is_default=True
    )


# --- project access --------------------------------------------------------

def project_team_ids(project) -> set[int]:
    if project is None or project.pk is None:
        return set()
    return set(project.teams.values_list("id", flat=True))


def has_project_team_access(user, project) -> bool:
    """Does ``user`` reach ``project`` through a team grant?

    Independent of the feature flag so it stays testable on its own; callers
    decide whether to consult it. A project with **no** teams granted returns
    ``False`` — absence of a grant is not a grant, and the legacy rule (which
    the caller still applies) is what keeps such projects reachable.
    """
    if project is None or user is None or not getattr(user, "is_authenticated", False):
        return False
    granted = project_team_ids(project)
    if not granted:
        return False
    return bool(granted & user_team_ids(user))


def is_eligible_project_assignee(user, project) -> bool:
    """Whether ``user`` belongs to ``project``'s one working team.

    Assignment is stricter than legacy browse access: no working team means no
    eligible assignees. This predicate intentionally does not depend
    on ``FEATURE_TEAMS`` because team-first assignment is now the write-path
    invariant even while older read paths retain their rollout compatibility.
    """
    if project is None or user is None or not getattr(user, "is_authenticated", False):
        return False
    return bool(
        project.working_team_id
        and TeamMembership.objects.filter(
            team_id=project.working_team_id, user=user
        ).exists()
    )


@transaction.atomic
def set_project_working_team(project, team, *, actor=None) -> dict:
    """Bind one assignment team and withdraw assignees outside it.

    Members shared with the replacement team keep their assignments; everyone
    else receives a cancelled withdrawal record and their task becomes
    unassigned. The team is also present in the broader project grant relation.
    """
    from annotation.services import withdraw_project_assignments

    previous = project.working_team
    summary = withdraw_project_assignments(
        project,
        team_name=previous.name if previous else "Previous working team",
        retain_team=team,
        reason="Working team changed",
    )
    if team is not None:
        grant_project_team(project, team, actor=actor)
    project.working_team = team
    project.save(update_fields=["working_team"])
    if team is not None:
        from projects.models import ProjectMembership

        ProjectMembership.objects.bulk_create(
            [
                ProjectMembership(project=project, user_id=user_id, added_by=actor)
                for user_id in team.memberships.values_list("user_id", flat=True)
            ],
            ignore_conflicts=True,
        )
    record_audit(
        actor,
        AuditVerb.PROJECT_TEAM_GRANTED,
        project,
        previous_team_id=previous.id if previous else None,
        team_id=team.id if team else None,
        withdrawn=summary["withdrawn"],
    )
    return summary


def team_delete_impact(team) -> dict:
    projects = list(team.working_projects.order_by("title"))
    rows = []
    total = 0
    for project in projects:
        # The working team owns the project assignment pool. Count every live
        # assignment, including any legacy outsider row that predates the
        # current write-path validation, because deletion unwinds all of them.
        count = project.tasks.filter(assigned_to__isnull=False).count()
        total += count
        rows.append({"id": project.id, "title": project.title, "task_count": count})
    return {
        "project_count": len(projects),
        "task_count": total,
        "projects": rows,
    }


@transaction.atomic
def delete_team_and_withdraw(team, *, actor=None) -> dict:
    """Delete ``team`` after safely unwinding every project it works on."""
    from annotation.services import withdraw_project_assignments

    impact = team_delete_impact(team)
    for project in list(team.working_projects.all()):
        withdraw_project_assignments(
            project,
            team_name=team.name,
            reason="Working team deleted by manager",
        )
        project.working_team = None
        project.save(update_fields=["working_team"])
    team_id, team_name = team.id, team.name
    record_audit(
        actor, AuditVerb.PROJECT_TEAM_REVOKED, team,
        team_name=team_name,
        project_count=impact["project_count"],
        task_count=impact["task_count"],
    )
    team.delete()
    return {**impact, "team_id": team_id, "team_name": team_name}


@transaction.atomic
def grant_project_team(project, team, *, actor=None) -> None:
    if not project.teams.filter(pk=team.pk).exists():
        project.teams.add(team)
        record_audit(
            actor, AuditVerb.PROJECT_TEAM_GRANTED, project,
            team_id=team.id, team_name=team.name,
        )
    # Backwards-compatible first grant: legacy callers that know only the
    # access grant API still establish the project's one assignment team.
    if project.working_team_id is None:
        project.working_team = team
        project.save(update_fields=["working_team"])


@transaction.atomic
def revoke_project_team(project, team, *, actor=None) -> None:
    if not project.teams.filter(pk=team.pk).exists():
        return
    if project.working_team_id == team.id:
        set_project_working_team(project, None, actor=actor)
    project.teams.remove(team)
    record_audit(
        actor, AuditVerb.PROJECT_TEAM_REVOKED, project,
        team_id=team.id, team_name=team.name,
    )
