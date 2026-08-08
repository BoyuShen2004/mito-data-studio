"""Manager share lifecycle and anonymous nested-browse metadata."""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsManager
from volumes.models import Volume

from .models import Dataset, Project, PublicShare


def share_payload(row):
    return {
        "id": row.id, "token": row.token, "scope": row.scope,
        "project_id": row.project_id, "project_title": row.project.title,
        "dataset_id": row.dataset_id, "dataset_name": row.dataset.name if row.dataset else "",
        "volume_id": row.volume_id, "volume_name": row.volume.name if row.volume else "",
        "created_at": row.created_at, "revoked_at": row.revoked_at,
        "created_by": row.created_by_id,
        "created_by_username": row.created_by.get_username() if row.created_by else "",
        "url": f"/share/public/{row.token}",
    }


def _active_payload(rows):
    return [share_payload(row) for row in rows if row.revoked_at is None]


def _volume_node(volume, shares):
    direct = [row for row in shares if row.scope == PublicShare.Scope.VOLUME and row.volume_id == volume.id]
    live = _active_payload(direct)
    return {
        "id": volume.id,
        "name": volume.name,
        "has_region_mask": volume.has_region_mask,
        "region_mask_coverage": volume.region_mask_coverage,
        "shared": bool(live),
        "direct_shares": live,
    }


def _aggregate_state(*, direct, children):
    if direct:
        return "all"
    if children and all(child in ("all", "shared") for child in children):
        return "all"
    if any(child != "none" and child != "not_shared" for child in children):
        return "partial"
    return "none"


def share_tree():
    """One query-prefetched manager tree with direct and aggregate state.

    A parent Stop operates on its direct scope only. Child shares remain live,
    so the aggregate LED can move from all to partial after that Stop.
    """
    projects = Project.objects.prefetch_related(
        "datasets__volumes", "volumes", "public_shares__dataset",
        "public_shares__volume", "public_shares__created_by",
    ).all()
    result = []
    for project in projects:
        shares = list(project.public_shares.all())
        project_direct = _active_payload(
            [row for row in shares if row.scope == PublicShare.Scope.PROJECT]
        )
        datasets = []
        dataset_ids = set()
        for dataset in project.datasets.all():
            dataset_ids.add(dataset.id)
            dataset_direct = _active_payload([
                row for row in shares
                if row.scope == PublicShare.Scope.DATASET and row.dataset_id == dataset.id
            ])
            volumes = [_volume_node(volume, shares) for volume in dataset.volumes.all()]
            state = _aggregate_state(
                direct=dataset_direct,
                children=["shared" if volume["shared"] else "not_shared" for volume in volumes],
            )
            datasets.append({
                "id": dataset.id, "name": dataset.name, "state": state,
                "direct_shares": dataset_direct, "volumes": volumes,
            })
        ungrouped = [
            _volume_node(volume, shares)
            for volume in project.volumes.all()
            if volume.dataset_id not in dataset_ids
        ]
        child_states = [dataset["state"] for dataset in datasets] + [
            "shared" if volume["shared"] else "not_shared" for volume in ungrouped
        ]
        result.append({
            "id": project.id, "title": project.title,
            "state": _aggregate_state(direct=project_direct, children=child_states),
            "direct_shares": project_direct,
            "datasets": datasets, "ungrouped_volumes": ungrouped,
        })
    return result


class PublicShareAdminView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounts.roles import is_manager
        if not is_manager(request.user):
            return Response({"detail": "Manager access is required."}, status=403)
        rows = PublicShare.objects.select_related("project", "dataset", "volume").all()
        return Response([share_payload(row) for row in rows])

    def post(self, request):
        scope = request.data.get("scope")
        project = get_object_or_404(Project, pk=request.data.get("project_id"))
        dataset = volume = None
        if scope == PublicShare.Scope.DATASET:
            dataset = get_object_or_404(Dataset, pk=request.data.get("dataset_id"), project=project)
        elif scope == PublicShare.Scope.VOLUME:
            volume = get_object_or_404(Volume, pk=request.data.get("volume_id"), project=project)
            dataset = volume.dataset
        elif scope != PublicShare.Scope.PROJECT:
            return Response({"detail": "scope must be project, dataset, or volume."}, status=400)
        from accounts.roles import is_manager
        if not is_manager(request.user):
            from annotation.services import can_view_volume
            if scope != PublicShare.Scope.VOLUME or not can_view_volume(request.user, volume):
                return Response({"detail": "Only managers can share projects or datasets."}, status=403)
        filters = {"scope": scope, "project": project, "revoked_at__isnull": True}
        if scope == PublicShare.Scope.DATASET:
            filters["dataset"] = dataset
        elif scope == PublicShare.Scope.VOLUME:
            filters["volume"] = volume
        row = PublicShare.objects.filter(**filters).select_related("project", "dataset", "volume", "created_by").first()
        if row is None:
            row = PublicShare.objects.create(scope=scope, project=project, dataset=dataset, volume=volume, created_by=request.user)
        return Response(share_payload(row), status=201)


class PublicShareRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from accounts.roles import is_manager
        row = get_object_or_404(PublicShare, pk=pk)
        if not is_manager(request.user) and (
            row.scope != PublicShare.Scope.VOLUME or row.created_by_id != request.user.id
        ):
            return Response({"detail": "Only managers or the annotator who opened a volume share can stop it."}, status=403)
        if row.revoked_at is None:
            row.revoked_at, row.revoked_by = timezone.now(), request.user
            row.save(update_fields=["revoked_at", "revoked_by"])
        return Response(share_payload(row))


class PublicShareTreeView(APIView):
    permission_classes = [IsManager]

    def get(self, request):
        return Response({"projects": share_tree(), "stop_policy": "direct_scope_only"})


class PublicShareEntityView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        scope = request.query_params.get("scope")
        project = get_object_or_404(Project, pk=request.query_params.get("project_id"))
        dataset = volume = None
        rows = PublicShare.objects.filter(project=project, scope=scope, revoked_at__isnull=True).select_related("project", "dataset", "volume", "created_by")
        if scope == PublicShare.Scope.DATASET:
            dataset = get_object_or_404(Dataset, pk=request.query_params.get("dataset_id"), project=project)
            rows = rows.filter(dataset=dataset)
        elif scope == PublicShare.Scope.VOLUME:
            volume = get_object_or_404(Volume, pk=request.query_params.get("volume_id"), project=project)
            rows = rows.filter(volume=volume)
        elif scope != PublicShare.Scope.PROJECT:
            return Response({"detail": "Unknown share scope."}, status=400)
        from accounts.roles import is_manager
        if not is_manager(request.user):
            from annotation.services import can_view_volume
            if scope != PublicShare.Scope.VOLUME or not can_view_volume(request.user, volume):
                return Response({"detail": "You cannot view this share state."}, status=403)
        aggregate_state = "shared" if rows.exists() else "not_shared"
        if scope in (PublicShare.Scope.PROJECT, PublicShare.Scope.DATASET):
            project_node = next(node for node in share_tree() if node["id"] == project.id)
            if scope == PublicShare.Scope.PROJECT:
                aggregate_state = project_node["state"]
            else:
                aggregate_state = next(
                    node["state"] for node in project_node["datasets"] if node["id"] == dataset.id
                )
        return Response({"scope": scope, "active": rows.exists(), "aggregate_state": aggregate_state, "shares": [share_payload(row) for row in rows]})


class PublicShareBrowseView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        row = PublicShare.objects.select_related("project", "dataset", "volume").filter(token=token).first()
        if row is None:
            return Response({"detail": "Share not found."}, status=404)
        if row.revoked_at:
            return Response({"detail": "The manager closed this share.", "closed": True}, status=410)
        datasets = row.project.datasets.all()
        volumes = Volume.objects.filter(project=row.project).select_related("dataset")
        if row.scope == PublicShare.Scope.DATASET:
            datasets, volumes = datasets.filter(pk=row.dataset_id), volumes.filter(dataset_id=row.dataset_id)
        elif row.scope == PublicShare.Scope.VOLUME:
            datasets, volumes = datasets.filter(pk=row.dataset_id), volumes.filter(pk=row.volume_id)
        return Response({
            **share_payload(row),
            "datasets": [{"id": d.id, "name": d.name, "description": d.description} for d in datasets],
            "volumes": [
                {
                    "id": v.id,
                    "dataset_id": v.dataset_id,
                    "name": v.name,
                    "shape": [v.shape_z, v.shape_y, v.shape_x],
                    "file_format": v.file_format,
                    "voxel_size": [v.voxel_size_z, v.voxel_size_y, v.voxel_size_x],
                    "label_type": v.label_type,
                    "has_region_mask": v.has_region_mask,
                    "region_mask_coverage": v.region_mask_coverage,
                }
                for v in volumes
            ],
        })
