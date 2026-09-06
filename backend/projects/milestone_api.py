"""HTTP surface for milestones and the delivery analytics that read them.

Milestones are manager-owned: the working group can see where a project stands,
but only a manager (or the requester who owns the project) sets the targets.
Every number returned here comes from ``core.statistics``, which knows nothing
about HTTP, so the same figures are reachable from a command or a test.

Returns 503 when ``FEATURE_MILESTONES`` is off, matching the convention the
dashboards and chunk service already follow.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.audit import record_audit
from accounts.roles import is_manager
from core.choices import AuditVerb
from core.statistics import (
    annotator_productivity,
    attention_queue,
    burndown,
    delivery_enabled,
    milestone_progress,
    throughput_series,
)

from .models import Milestone, Project

_DISABLED = Response(
    {"detail": "Milestones are not enabled.", "reason": "disabled"},
    status=status.HTTP_503_SERVICE_UNAVAILABLE,
)


def _may_view(user, project) -> bool:
    """Reuses project membership so delivery visibility cannot drift from task
    visibility — and so team access works here automatically."""
    from annotation.services import is_project_member

    return is_project_member(user, project)


def _may_edit(user, project) -> bool:
    """Managers, or the requester who owns the project."""
    return is_manager(user) or project.created_by_id == getattr(user, "id", None)


class MilestoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = Milestone
        fields = [
            "id", "project", "name", "description", "due_on", "order",
            "status", "volumes", "target_metric", "target_value",
            "completed_at", "created_at",
        ]
        read_only_fields = ["id", "project", "completed_at", "created_at"]

    def validate_target_value(self, value):
        if value < 0:
            raise serializers.ValidationError("A target cannot be negative.")
        return value

    def validate_volumes(self, volumes):
        """A milestone may only scope volumes that belong to its own project.

        Without this, scoping a milestone to another project's volume would
        silently produce a target that can never be met, because
        ``milestone_progress`` filters by project *and* by the scoped ids.
        """
        project = self.context.get("project")
        if project is None:
            return volumes
        foreign = [v.pk for v in volumes if v.project_id != project.pk]
        if foreign:
            raise serializers.ValidationError(
                f"Volumes {foreign} do not belong to this project."
            )
        return volumes


class ProjectMilestonesView(APIView):
    """``GET`` the project's milestones with live progress; ``POST`` a new one."""

    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        if not delivery_enabled():
            return _DISABLED
        project = get_object_or_404(Project, pk=project_id)
        if not _may_view(request.user, project):
            return Response(
                {"detail": "You do not have access to this project."},
                status=status.HTTP_403_FORBIDDEN,
            )
        milestones = project.milestones.prefetch_related("volumes")
        return Response(
            {
                "results": [
                    {
                        **MilestoneSerializer(milestone).data,
                        "progress": milestone_progress(milestone),
                    }
                    for milestone in milestones
                ],
                "can_edit": _may_edit(request.user, project),
            }
        )

    def post(self, request, project_id):
        if not delivery_enabled():
            return _DISABLED
        project = get_object_or_404(Project, pk=project_id)
        if not _may_edit(request.user, project):
            return Response(
                {"detail": "Only a manager or the project owner may set milestones."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = MilestoneSerializer(
            data=request.data, context={"project": project}
        )
        serializer.is_valid(raise_exception=True)
        milestone = serializer.save(project=project, created_by=request.user)
        record_audit(
            request.user, AuditVerb.MILESTONE_CREATED, target=milestone,
            project_id=project.pk,
        )
        return Response(
            {**MilestoneSerializer(milestone).data,
             "progress": milestone_progress(milestone)},
            status=status.HTTP_201_CREATED,
        )


class MilestoneDetailView(APIView):
    """``GET``/``PATCH``/``DELETE`` one milestone."""

    permission_classes = [IsAuthenticated]

    def _milestone(self, pk):
        return get_object_or_404(
            Milestone.objects.select_related("project"), pk=pk
        )

    def get(self, request, pk):
        if not delivery_enabled():
            return _DISABLED
        milestone = self._milestone(pk)
        if not _may_view(request.user, milestone.project):
            return Response(
                {"detail": "You do not have access to this project."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(
            {
                **MilestoneSerializer(milestone).data,
                "progress": milestone_progress(milestone),
                "burndown": burndown(milestone),
            }
        )

    def patch(self, request, pk):
        if not delivery_enabled():
            return _DISABLED
        milestone = self._milestone(pk)
        if not _may_edit(request.user, milestone.project):
            return Response(
                {"detail": "Only a manager or the project owner may edit milestones."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = MilestoneSerializer(
            milestone, data=request.data, partial=True,
            context={"project": milestone.project},
        )
        serializer.is_valid(raise_exception=True)
        milestone = serializer.save()
        record_audit(request.user, AuditVerb.MILESTONE_UPDATED, target=milestone)
        return Response(
            {**MilestoneSerializer(milestone).data,
             "progress": milestone_progress(milestone)}
        )

    def delete(self, request, pk):
        if not delivery_enabled():
            return _DISABLED
        milestone = self._milestone(pk)
        if not _may_edit(request.user, milestone.project):
            return Response(
                {"detail": "Only a manager or the project owner may delete milestones."},
                status=status.HTTP_403_FORBIDDEN,
            )
        record_audit(
            request.user, AuditVerb.MILESTONE_DELETED, target=milestone,
            name=milestone.name,
        )
        milestone.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeliveryOverviewView(APIView):
    """``GET /api/delivery/`` — the same panels, across every project.

    ``core.statistics`` already accepts ``project=None`` for all three; this
    endpoint is what makes that reachable. Without it a manager had to open
    each project in turn to find out which one had gone overdue, which is the
    opposite of what an attention queue is for.

    Manager-only, deliberately: the per-project endpoint is scoped by
    membership, but "every project at once" has no membership to scope it by,
    and quietly returning a filtered subset would read as "nothing is
    overdue" to somebody who simply cannot see it.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not delivery_enabled():
            return _DISABLED
        if not is_manager(request.user):
            return Response(
                {"detail": "Manager access required."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            days = int(request.query_params.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        return Response(
            {
                "throughput": throughput_series(None, days=days),
                "productivity": annotator_productivity(None),
                "attention": attention_queue(None),
            }
        )


class ProjectDeliveryView(APIView):
    """``GET /api/projects/<id>/delivery/`` — throughput, people, attention.

    One endpoint rather than three because the Analytics tab renders all of it
    at once; splitting it would only cost the client two more round trips.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        if not delivery_enabled():
            return _DISABLED
        project = get_object_or_404(Project, pk=project_id)
        if not _may_view(request.user, project):
            return Response(
                {"detail": "You do not have access to this project."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            days = int(request.query_params.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        return Response(
            {
                "throughput": throughput_series(project, days=days),
                "productivity": annotator_productivity(project),
                "attention": attention_queue(project),
            }
        )
