from django.contrib.auth import get_user_model
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from accounts.roles import is_manager, is_requester
from annotation.services import calculate_annotator_workload
from core.lifecycle import (
    Lifecycle,
    filter_projects_by_lifecycle,
    project_lifecycle_counts,
)
from core.permissions import CanViewProjectData

from .models import Dataset, Project, ProjectMembership
from .serializers import DatasetSerializer, ProjectMembershipSerializer, ProjectSerializer
from .services import (
    DeleteBlocked,
    calculate_project_progress,
    create_project,
    delete_dataset,
    delete_project,
    describe_dataset_dependents,
    describe_project_dependents,
    ensure_dataset_folder,
    mark_project_reviewed,
    update_dataset,
)


def _forced(request) -> bool:
    """Whether the caller explicitly confirmed a destructive delete."""
    value = request.query_params.get("force") or request.data.get("force")
    return str(value).lower() in ("1", "true", "yes")


class ProjectViewSet(viewsets.ModelViewSet):
    """CRUD for projects plus a progress/summary action.

    Managers see and manage every project. Requesters see and manage only the
    projects they created. Editing/deleting is restricted to managers and the
    owning requester (enforced by the per-object queryset below).
    """

    serializer_class = ProjectSerializer
    permission_classes = [CanViewProjectData]

    def get_queryset(self):
        qs = Project.objects.select_related("institution", "created_by").all()
        if is_requester(self.request.user):
            # Requesters (Institutions) only see their own projects.
            qs = qs.filter(created_by=self.request.user)
        elif not is_manager(self.request.user):
            qs = qs.filter(
                tasks__assigned_to=self.request.user
            ).distinct()
        # Optional lifecycle filter: ?lifecycle=new|to_proofread|done.
        lifecycle = self.request.query_params.get("lifecycle")
        if lifecycle in Lifecycle.values:
            qs = filter_projects_by_lifecycle(qs, lifecycle)
        return qs

    def perform_create(self, serializer):
        data = serializer.validated_data
        project = create_project(
            title=data["title"],
            created_by=self.request.user,
            institution=data.get("institution"),
            description=data.get("description", ""),
            annotation_target=data.get("annotation_target", "mitochondria"),
            annotation_type=data.get("annotation_type"),
            workflow_type=data.get("workflow_type"),
            deadline=data.get("deadline"),
            status=data.get("status"),
            dataset=data.get("dataset", ""),
            metadata=data.get("metadata"),
            # Manager-created projects are reviewed on creation.
            reviewed=is_manager(self.request.user),
        )
        serializer.instance = project

    @action(detail=False, methods=["get"], url_path="lifecycle-counts")
    def lifecycle_counts(self, request):
        """Return {new, to_proofread, done} counts over the caller's projects.

        Respects the same visibility rules as the list endpoint: managers see
        every project, Institutions see only their own.
        """
        # Bypass any ?lifecycle= filter so every bucket is counted.
        qs = Project.objects.select_related("institution", "created_by")
        if is_requester(request.user):
            qs = qs.filter(created_by=request.user)
        elif not is_manager(request.user):
            qs = qs.filter(tasks__assigned_to=request.user).distinct()
        return Response(project_lifecycle_counts(qs))

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        project = self.get_object()
        payload = {
            "project": ProjectSerializer(
                project, context=self.get_serializer_context()
            ).data,
            "progress": calculate_project_progress(project),
        }
        # Workload is manager-facing detail; requesters just see progress.
        if is_manager(request.user):
            payload["workload"] = calculate_annotator_workload(project=project)
        return Response(payload)

    @action(detail=True, methods=["get"])
    def dependents(self, request, pk=None):
        """What a delete would take with it, so the UI can warn accurately."""
        return Response(describe_project_dependents(self.get_object()))

    def destroy(self, request, *args, **kwargs):
        """Delete a project, refusing to discard annotation work by accident."""
        project = self.get_object()
        try:
            counts = delete_project(project, force=_forced(request))
        except DeleteBlocked as exc:
            return Response(
                {"detail": str(exc), "counts": exc.counts},
                status=status.HTTP_409_CONFLICT,
            )
        return Response({"deleted": counts}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        """Manager marks a project reviewed (or not), enabling assignment."""
        if not is_manager(request.user):
            return Response(
                {"detail": "Manager access required."},
                status=status.HTTP_403_FORBIDDEN,
            )
        project = self.get_object()
        reviewed = request.data.get("reviewed", True)
        mark_project_reviewed(project, reviewer=request.user, reviewed=bool(reviewed))
        return Response(ProjectSerializer(project).data)

    @action(detail=True, methods=["get", "post"], url_path="members")
    def members(self, request, pk=None):
        """List the working group or add explicit, zero-workload membership."""
        if not is_manager(request.user):
            raise PermissionDenied("Manager access required.")
        project = self.get_object()
        if request.method == "POST":
            user = get_user_model().objects.filter(
                pk=request.data.get("user_id"), is_active=True
            ).first()
            if user is None:
                return Response({"detail": "Choose an active user."}, status=400)
            from accounts.roles import get_role
            from accounts.teams import ensure_project_assignee_eligible
            from core.choices import UserRole

            if get_role(user) != UserRole.ANNOTATOR:
                return Response({"detail": "Choose an active annotator."}, status=400)
            was_explicit = ProjectMembership.objects.filter(
                project=project, user=user
            ).exists()
            _team_membership, membership = ensure_project_assignee_eligible(
                project, user, actor=request.user
            )
            return Response(
                ProjectMembershipSerializer(membership).data,
                status=status.HTTP_200_OK if was_explicit else status.HTTP_201_CREATED,
            )

        explicit = {
            row.user_id: row
            for row in project.memberships.select_related(
                "user", "user__profile", "added_by"
            )
        }
        assigned_ids = set(
            project.tasks.exclude(assigned_to__isnull=True).values_list(
                "assigned_to_id", flat=True
            )
        )
        working_team_ids = set()
        if project.working_team_id:
            working_team_ids = set(
                project.working_team.memberships.values_list("user_id", flat=True)
            )
        users = get_user_model().objects.filter(
            id__in=set(explicit) | working_team_ids | assigned_ids
        ).select_related("profile").order_by("username")
        return Response([
            {
                "user_id": user.id,
                "username": user.get_username(),
                "display_name": getattr(getattr(user, "profile", None), "display_name", ""),
                "is_explicit": user.id in explicit,
                "is_working_team": user.id in working_team_ids,
                "has_tasks": user.id in assigned_ids,
                "access_reason": (
                    "Project member" if user.id in explicit
                    else "Working team" if user.id in working_team_ids
                    else "Via assigned task"
                ),
                "membership_id": explicit[user.id].id if user.id in explicit else None,
                "created_at": explicit[user.id].created_at if user.id in explicit else None,
            }
            for user in users
        ])

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"members/(?P<user_id>\d+)",
    )
    def remove_member(self, request, pk=None, user_id=None):
        if not is_manager(request.user):
            raise PermissionDenied("Manager access required.")
        project = self.get_object()
        deleted, _ = ProjectMembership.objects.filter(
            project=project, user_id=user_id
        ).delete()
        if not deleted:
            return Response(
                {"detail": "This person has no explicit project membership."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class DatasetViewSet(viewsets.ModelViewSet):
    """CRUD for the datasets inside a project.

    Visibility follows the project: managers see every dataset, requesters only
    those in projects they created. ``?project=<id>`` narrows the list.
    """

    serializer_class = DatasetSerializer
    permission_classes = [CanViewProjectData]

    def get_queryset(self):
        qs = Dataset.objects.select_related("project").all()
        if is_requester(self.request.user):
            qs = qs.filter(project__created_by=self.request.user)
        elif not is_manager(self.request.user):
            qs = qs.filter(project__tasks__assigned_to=self.request.user).distinct()
        project_id = self.request.query_params.get("project")
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs

    def _check_project_access(self, project) -> None:
        if not is_manager(self.request.user) and project.created_by_id != self.request.user.id:
            raise PermissionDenied("You do not own this project.")

    def perform_create(self, serializer):
        self._check_project_access(serializer.validated_data["project"])
        serializer.save()
        ensure_dataset_folder(serializer.instance.project, serializer.instance)

    def perform_update(self, serializer):
        # Guard both the current project and any project it is moved to.
        self._check_project_access(serializer.instance.project)
        target = serializer.validated_data.get("project")
        if target is not None:
            self._check_project_access(target)
        update_dataset(serializer.instance, **serializer.validated_data)

    @action(detail=True, methods=["get"])
    def dependents(self, request, pk=None):
        return Response(describe_dataset_dependents(self.get_object()))

    def destroy(self, request, *args, **kwargs):
        dataset = self.get_object()
        self._check_project_access(dataset.project)
        try:
            counts = delete_dataset(dataset, force=_forced(request))
        except DeleteBlocked as exc:
            return Response(
                {"detail": str(exc), "counts": exc.counts},
                status=status.HTTP_409_CONFLICT,
            )
        return Response({"deleted": counts}, status=status.HTTP_200_OK)
