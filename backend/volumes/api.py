from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.roles import is_manager, is_requester
from core.permissions import CanRegisterData, CanViewProjectData, IsManager
from projects.models import Project
from processing.models import ProcessingJob
from projects.serializers import ProjectSerializer
from projects.services import (
    DeleteBlocked,
    delete_volume,
    describe_volume_dependents,
)

from .models import Volume
from .pyramid.jobs import DuplicateBuild, PyramidBuildSpec, PyramidJobError, submit_build
from .serializers import (
    HpcScanSerializer,
    RegisterDataSerializer,
    VolumeSerializer,
)
from .services import (
    DataRegistrationError,
    register_dataset,
    register_volume,
    scan_data_sources,
    update_volume_metadata,
)


class HpcScanView(APIView):
    """Scan an image directory + optional mask directory. Requesters + managers.

    Returns the files on each side already paired by case id, so the client
    never has to match names itself.
    """

    permission_classes = [CanRegisterData]

    def post(self, request):
        serializer = HpcScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = scan_data_sources(
                data.get("image_directory") or data.get("hpc_directory") or "",
                data.get("mask_directory") or "",
                data.get("region_mask_directory") or "",
                auto_pair=True,
            )
        except DataRegistrationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class RegisterDataView(APIView):
    """Shared data-registration endpoint for requesters and managers.

    Registers references to ``.tif``/``.tiff``/``.nii.gz`` files that already
    live in an HPC directory as volume pairs, under a dataset of an **existing**
    project. New work starts by creating the project (``POST /api/projects/``),
    then registering one or more datasets into it. No browser upload is required.
    """

    permission_classes = [CanRegisterData]

    def post(self, request):
        serializer = RegisterDataSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        project = get_object_or_404(Project, pk=data["project"])
        # Requesters may only add to their own projects.
        if not is_manager(request.user) and project.created_by_id != request.user.id:
            return Response(
                {"detail": "You do not own this project."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            project, volumes = register_dataset(
                created_by=request.user,
                dataset=data["dataset"],
                volume=data["volume"],
                image_directory=data.get("image_directory") or "",
                region_mask_directory=data.get("region_mask_directory") or "",
                mask_directory=data.get("mask_directory") or "",
                hpc_directory=data.get("hpc_directory") or "",
                pairs=data.get("pairs"),
                files=data.get("files"),
                # Pass None (not "none") when the client omits it, so a mask
                # can default to `prediction` instead of being rejected.
                label_type=data.get("label_type") or None,
                metadata=data.get("metadata"),
                project=project,
                annotation_type=data.get("annotation_type") or None,
                # Manager-registered data is reviewed on creation; requester data
                # stays pending until a manager approves it.
                reviewed=is_manager(request.user),
            )
        except DataRegistrationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "project": ProjectSerializer(project).data,
                "volumes": VolumeSerializer(volumes, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )


class ProjectVolumesView(generics.ListCreateAPIView):
    """List a project's volumes, or register a new one.

    Managers act on any project; requesters only on projects they own. Listing
    is permitted for either; creating requires ownership (or manager).
    """

    serializer_class = VolumeSerializer
    permission_classes = [CanViewProjectData]
    parser_classes = [MultiPartParser, FormParser]

    def get_project(self) -> Project:
        project = get_object_or_404(Project, pk=self.kwargs["project_id"])
        if (
            is_requester(self.request.user)
            and project.created_by_id != self.request.user.id
        ):
            raise PermissionDenied("You do not have access to this project.")
        if (
            not is_manager(self.request.user)
            and not is_requester(self.request.user)
            and not project.tasks.filter(assigned_to=self.request.user).exists()
        ):
            raise PermissionDenied("You do not have access to this project.")
        return project

    def get_queryset(self):
        self.get_project()  # enforces access
        return Volume.objects.filter(
            project_id=self.kwargs["project_id"]
        ).prefetch_related(
            Prefetch(
                "processing_jobs",
                queryset=ProcessingJob.objects.filter(
                    job_type="build_pyramid"
                ).order_by("-created_at"),
                to_attr="pyramid_jobs",
            )
        )

    def create(self, request, *args, **kwargs):
        project = self.get_project()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        voxel_size = None
        if data.get("voxel_size_z") is not None:
            voxel_size = (
                data.get("voxel_size_z"),
                data.get("voxel_size_y"),
                data.get("voxel_size_x"),
            )
        volume = register_volume(
            project=project,
            name=data["name"],
            image_path=data.get("image_path", ""),
            image_file=data.get("image_file"),
            region_mask_path=data.get("region_mask_path", ""),
            region_mask_file=data.get("region_mask_file"),
            label_path=data.get("label_path", ""),
            label_file=data.get("label_file"),
            label_type=data.get("label_type") or None,
            file_format=data.get("file_format"),
            voxel_size=voxel_size,
            metadata=data.get("metadata"),
            created_by=request.user,
        )
        out = VolumeSerializer(volume, context=self.get_serializer_context())
        return Response(out.data, status=status.HTTP_201_CREATED)


class VolumeDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, edit, or delete a volume.

    Viewing is allowed for managers and the owning requester; editing and
    deleting are likewise restricted to managers and the project owner. A
    volume with annotation work is only deleted when explicitly forced.
    """

    queryset = Volume.objects.all()
    serializer_class = VolumeSerializer
    permission_classes = [CanViewProjectData]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_object(self) -> Volume:
        volume = super().get_object()
        if (
            is_requester(self.request.user)
            and volume.project.created_by_id != self.request.user.id
        ):
            raise PermissionDenied("You do not have access to this volume.")
        if (
            not is_manager(self.request.user)
            and not is_requester(self.request.user)
            and not volume.tasks.filter(assigned_to=self.request.user).exists()
        ):
            raise PermissionDenied("You do not have access to this volume.")
        return volume

    def update(self, request, *args, **kwargs):
        volume = self.get_object()
        serializer = self.get_serializer(volume, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        # Moving a volume between datasets is only allowed within reach.
        dataset = data.get("dataset")
        if dataset is not None and not is_manager(request.user) and (
            dataset.project.created_by_id != request.user.id
        ):
            raise PermissionDenied("You do not own the target dataset.")
        update_volume_metadata(volume, **data)
        volume.refresh_from_db()
        return Response(VolumeSerializer(volume).data)

    def destroy(self, request, *args, **kwargs):
        volume = self.get_object()
        force = str(
            request.query_params.get("force") or request.data.get("force")
        ).lower() in ("1", "true", "yes")
        try:
            counts = delete_volume(volume, force=force)
        except DeleteBlocked as exc:
            return Response(
                {"detail": str(exc), "counts": exc.counts},
                status=status.HTTP_409_CONFLICT,
            )
        return Response({"deleted": counts}, status=status.HTTP_200_OK)


class VolumeDependentsView(APIView):
    """What deleting a volume would take with it."""

    permission_classes = [CanRegisterData]

    def get(self, request, pk):
        volume = get_object_or_404(Volume, pk=pk)
        if not is_manager(request.user) and (
            volume.project.created_by_id != request.user.id
        ):
            raise PermissionDenied("You do not have access to this volume.")
        return Response(describe_volume_dependents(volume))


class VolumePyramidBuildView(APIView):
    """Queue a manager-requested build or rebuild of a read-only pyramid layer.

    ``?layer=region`` (or ``{"layer": "region"}``) builds the ROI derivative;
    the default stays the image so existing clients keep working.
    """

    permission_classes = [IsManager]

    def post(self, request, pk):
        volume = get_object_or_404(Volume, pk=pk)
        layer = str(
            request.data.get("layer") or request.query_params.get("layer") or "image"
        )
        if layer not in ("image", "region"):
            return Response(
                {"detail": f"Unknown layer {layer!r}.", "reason": "unknown_layer"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if layer == "region" and not volume.has_region_mask:
            return Response(
                {
                    "detail": "This volume has no region mask to build.",
                    "reason": "no_region_mask",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            job = submit_build(
                PyramidBuildSpec(
                    volume_id=volume.pk,
                    layer=layer,
                    metadata={"trigger": "manager_rebuild"},
                ),
                actor=request.user,
            )
        except DuplicateBuild as exc:
            return Response(
                {
                    "detail": str(exc),
                    "reason": exc.reason,
                    "job_id": exc.job_id,
                },
                status=status.HTTP_409_CONFLICT,
            )
        except PyramidJobError as exc:
            code = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.reason == "disabled"
                else status.HTTP_400_BAD_REQUEST
            )
            return Response(
                {"detail": str(exc), "reason": exc.reason}, status=code
            )
        volume.refresh_from_db()
        return Response(
            {"job_id": job.pk, "volume": VolumeSerializer(volume).data},
            status=status.HTTP_202_ACCEPTED,
        )
