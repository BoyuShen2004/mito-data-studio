"""Read + manage endpoints for processing jobs.

Managers see and manage every job; Institutions see jobs on the projects they
own (read-only). Retry/cancel are manager-only and call the service layer.
"""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.roles import is_manager

from .models import ProcessingJob
from .serializers import ProcessingJobSerializer
from .services import cancel_job, retry_job


class ProcessingJobViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProcessingJobSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = ProcessingJob.objects.select_related("project", "volume", "task").all()
        if is_manager(self.request.user):
            pass
        else:
            # Institutions see jobs for projects they created only.
            qs = qs.filter(project__created_by=self.request.user)
        job_type = self.request.query_params.get("job_type")
        if job_type:
            qs = qs.filter(job_type=job_type)
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    def _require_manager(self):
        return is_manager(self.request.user)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        if not self._require_manager():
            return Response(
                {"detail": "Manager access required."},
                status=status.HTTP_403_FORBIDDEN,
            )
        job = self.get_object()
        try:
            retry_job(job)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ProcessingJobSerializer(job).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        if not self._require_manager():
            return Response(
                {"detail": "Manager access required."},
                status=status.HTTP_403_FORBIDDEN,
            )
        job = self.get_object()
        cancel_job(job)
        return Response(ProcessingJobSerializer(job).data)
