"""Manager SPA surface for simple annotator teams and project grants."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from core.choices import TeamRole, UserRole
from core.permissions import IsManager
from projects.models import Project

from .models import Institution, Team, UserProfile
from .roles import get_role
from .teams import (
    add_team_member,
    delete_team_and_withdraw,
    grant_project_team,
    remove_team_member,
    revoke_project_team,
    set_project_working_team,
    team_delete_impact,
)


def _payload(*, mutation=None):
    users = get_user_model().objects.filter(is_active=True).select_related("profile").order_by("username")
    teams = Team.objects.select_related("organization").prefetch_related("memberships__user").all()
    return {
        "institutions": [{"id": row.id, "name": row.name} for row in Institution.objects.all()],
        "users": [
            {"id": user.id, "username": user.get_username(), "role": get_role(user) or ""}
            for user in users
        ],
        "teams": [
            {
                "id": team.id, "name": team.name, "description": team.description,
                "organization_id": team.organization_id,
                "organization_name": team.organization.name,
                "delete_impact": team_delete_impact(team),
                "members": [
                    {"user_id": m.user_id, "username": m.user.get_username(), "role": m.role}
                    for m in team.memberships.all()
                ],
            }
            for team in teams
        ],
        **({"mutation": mutation} if mutation is not None else {}),
    }


def _team_organization(request, project=None):
    """Infer the required legacy organization FK without UI ceremony.

    A project is authoritative in project context. The manager's organization
    is the natural People-hub fallback. Older API clients may still supply an
    organization id; installations without any of those get one deterministic
    internal organization instead of forcing managers to model institutions.
    """
    if project is not None and project.institution_id:
        return project.institution
    manager_organization_id = (
        UserProfile.objects.filter(user=request.user)
        .values_list("institution_id", flat=True).first()
    )
    if manager_organization_id:
        return Institution.objects.get(pk=manager_organization_id)
    explicit_id = request.data.get("organization_id")
    if explicit_id:
        return get_object_or_404(Institution, pk=explicit_id)
    organization, _ = Institution.objects.get_or_create(name="Mito Data Agent")
    return organization


def _annotators(member_ids):
    if member_ids is None:
        return []
    if not isinstance(member_ids, list):
        raise ValueError("member_ids must be a list of annotator ids.")
    try:
        wanted = {int(value) for value in member_ids}
    except (TypeError, ValueError):
        raise ValueError("member_ids must contain annotator ids.")
    users = list(
        get_user_model().objects.filter(pk__in=wanted, is_active=True)
        .select_related("profile").order_by("username")
    )
    found = {user.id for user in users}
    if found != wanted:
        raise ValueError("Every team member must be an active annotator.")
    if any(get_role(user) != UserRole.ANNOTATOR for user in users):
        raise ValueError("Every team member must be an annotator.")
    return users


class CollaborationAdminView(APIView):
    permission_classes = [IsManager]

    def get(self, request):
        return Response(_payload())

    def post(self, request):
        action = request.data.get("action")
        User = get_user_model()
        mutation = None
        try:
            if action == "create_team":
                project = None
                if request.data.get("project_id"):
                    project = get_object_or_404(Project, pk=request.data.get("project_id"))
                organization = _team_organization(request, project)
                name = str(request.data.get("name") or (project.title if project else "")).strip()
                if not name:
                    return Response({"detail": "Team name is required."}, status=400)
                members = _annotators(request.data.get("member_ids", []))
                with transaction.atomic():
                    team = Team.objects.create(organization=organization, name=name)
                    for user in members:
                        add_team_member(team, user, actor=request.user)
                    if project is not None:
                        set_project_working_team(project, team, actor=request.user)
                mutation = {"action": action, "team_id": team.id}
            elif action == "rename_team":
                team = get_object_or_404(Team, pk=request.data.get("team_id"))
                name = str(request.data.get("name") or "").strip()
                if not name:
                    return Response({"detail": "Team name is required."}, status=400)
                team.name = name
                team.save(update_fields=["name"])
            elif action in ("add_team_member", "remove_team_member"):
                team = get_object_or_404(Team, pk=request.data.get("team_id"))
                user = get_object_or_404(User, pk=request.data.get("user_id"))
                if action == "add_team_member":
                    if get_role(user) != UserRole.ANNOTATOR:
                        return Response({"detail": "Team members must be annotators."}, status=400)
                    add_team_member(team, user, role=request.data.get("role") or TeamRole.MEMBER, actor=request.user)
                else:
                    remove_team_member(team, user, actor=request.user)
            elif action in ("grant_project_team", "revoke_project_team"):
                project = get_object_or_404(Project, pk=request.data.get("project_id"))
                team = get_object_or_404(Team, pk=request.data.get("team_id"))
                if action == "grant_project_team":
                    grant_project_team(project, team, actor=request.user)
                else:
                    revoke_project_team(project, team, actor=request.user)
            elif action == "set_project_working_team":
                project = get_object_or_404(Project, pk=request.data.get("project_id"))
                team_id = request.data.get("team_id")
                team = get_object_or_404(Team, pk=team_id) if team_id else None
                summary = set_project_working_team(
                    project, team, actor=request.user
                )
                mutation = {
                    "action": action,
                    "project_id": project.id,
                    "team_id": team.id if team else None,
                    **summary,
                }
            elif action == "delete_team":
                team = get_object_or_404(Team, pk=request.data.get("team_id"))
                if request.data.get("confirm") is not True:
                    return Response(
                        {
                            "detail": "Team deletion requires explicit confirmation.",
                            "impact": team_delete_impact(team),
                        },
                        status=400,
                    )
                mutation = {
                    "action": action,
                    **delete_team_and_withdraw(team, actor=request.user),
                }
            else:
                return Response({"detail": "Unknown collaboration action."}, status=400)
        except (IntegrityError, TypeError, ValueError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(_payload(mutation=mutation))
