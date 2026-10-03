"""Authenticated volume-scoped measurement jobs; no label writes."""
import json
from pathlib import Path

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.roles import is_manager
from annotation.measurement_jobs import input_is_current, measurement_input
from annotation.measurements import MeasurementError
from annotation.services import can_view_volume
from core.choices import ACTIVE_JOB_STATUSES, ProcessingJobType
from processing.models import ProcessingJob
from processing.services import create_processing_job
from volumes.models import Volume


class MeasurementRequest(serializers.Serializer):
    source = serializers.ChoiceField(choices=("official", "working"))


def job_payload(job):
    if job is None:
        return None
    data = {
        "id": job.pk, "status": job.status, "source": job.config["source"],
        "created_at": job.created_at, "finished_at": job.finished_at,
        "error": job.error_message, "is_current": input_is_current(job), "result": None,
    }
    if job.status == "succeeded":
        try:
            data["result"] = json.loads(Path(job.output_paths["measurements"]).read_text())
        except (OSError, KeyError, ValueError):
            data["error"] = "The result file is unavailable. Run measurements again."
    return data


class VolumeMeasurementsView(APIView):
    permission_classes = [IsAuthenticated]

    def volume(self, request, pk):
        volume = get_object_or_404(Volume.objects.select_related("project", "dataset"), pk=pk)
        if not can_view_volume(request.user, volume):
            raise PermissionDenied("You do not have access to this volume.")
        return volume

    def get(self, request, pk):
        volume = self.volume(request, pk)
        params = MeasurementRequest(data={"source": request.query_params.get("source", "official")})
        params.is_valid(raise_exception=True)
        job = ProcessingJob.objects.select_related("volume__project", "volume__dataset").filter(
            volume=volume, job_type=ProcessingJobType.MEASURE_MITO,
            config__source=params.validated_data["source"],
        ).first()
        return Response({"job": job_payload(job)})

    def post(self, request, pk):
        if not is_manager(request.user):
            raise PermissionDenied("Only managers can run measurements. Existing results remain readable to volume members.")
        params = MeasurementRequest(data=request.data)
        params.is_valid(raise_exception=True)
        source = params.validated_data["source"]
        with transaction.atomic():
            volume = get_object_or_404(Volume.objects.select_for_update(), pk=pk)
            existing = ProcessingJob.objects.select_related("volume").filter(
                volume=volume, job_type=ProcessingJobType.MEASURE_MITO,
                status__in=("queued", *ACTIVE_JOB_STATUSES),
            ).first()
            if existing:
                return Response({"detail": "A measurement is already queued or running for this volume."}, status=409)
            try:
                input_spec = measurement_input(volume, source)
            except (MeasurementError, OSError) as exc:
                message = str(exc) if isinstance(exc, MeasurementError) else "The label file could not be read."
                return Response({"detail": message}, status=400)
            job = create_processing_job(
                job_type=ProcessingJobType.MEASURE_MITO, backend="local", volume=volume,
                project=volume.project, created_by=request.user,
                config={"source": source, "input": input_spec},
            )
        return Response({"job": job_payload(job)}, status=202)


class VolumeMeasurementSpacingView(VolumeMeasurementsView):
    http_method_names = ["get", "head", "options"]

    def get(self, request, pk):
        from annotation.measurement_spacing import measurement_spacing

        return Response(measurement_spacing(self.volume(request, pk)))
