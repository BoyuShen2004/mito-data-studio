"""HTTP surface for per-instance morphology and QA flags.

Thin by design: permission checks and serialization only. Every write goes
through ``annotation.instance_annotations.set_instance_annotation``, which owns
the delete-when-empty rule and the audit write, so no view can bypass them.

Every route below is registered unconditionally and returns 503 when
``FEATURE_INSTANCE_ANNOTATION`` is off — the convention the dashboards and
chunk service already follow, so a misconfiguration reads as "not enabled"
rather than as a 404 typo.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from projects.models import Project
from volumes.models import Volume

from .instance_annotations import (
    annotations_for_volume,
    instance_annotation_enabled,
    project_summary,
    set_instance_annotation,
    vocabulary,
)
from .models import AnnotationTask

_DISABLED = Response(
    {"detail": "Instance annotation is not enabled.", "reason": "disabled"},
    status=status.HTTP_503_SERVICE_UNAVAILABLE,
)


def serialize(row) -> dict:
    return {
        "label_id": row.label_id,
        "morphology": row.morphology,
        "qa_flags": list(row.qa_flags or []),
        "note": row.note,
        "review_worthy": row.review_worthy,
        "updated_by": row.updated_by.get_username() if row.updated_by else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class VolumeInstanceAnnotationsView(APIView):
    """``GET /api/volumes/<pk>/instance-annotations/``.

    Returns only the instances somebody has annotated. The client fills in the
    rest as unannotated, which keeps the response proportional to recorded
    information rather than to the number of objects in the volume.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if not instance_annotation_enabled():
            return _DISABLED
        volume = get_object_or_404(Volume, pk=pk)
        from .services import can_view_volume

        if not can_view_volume(request.user, volume):
            return Response(
                {"detail": "You do not have access to this volume."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(
            {
                "volume": volume.pk,
                "vocabulary": vocabulary(),
                "annotations": [
                    serialize(row) for row in annotations_for_volume(volume)
                ],
            }
        )


class TaskInstanceAnnotationsView(APIView):
    """``GET`` the task's volume map, ``PUT``/``DELETE`` one instance.

    Writes are keyed by task rather than by volume so the permission check is
    the ordinary ``can_annotate_task`` one, and an annotator cannot edit
    instance metadata on a volume they hold no task for.
    """

    permission_classes = [IsAuthenticated]

    def _task(self, request, pk, *, write: bool):
        task = get_object_or_404(
            AnnotationTask.objects.select_related("volume", "project"), pk=pk
        )
        from .services import can_annotate_task, can_view_task

        allowed = can_annotate_task(request.user, task) if write else can_view_task(
            request.user, task
        )
        return task, allowed

    def get(self, request, pk):
        if not instance_annotation_enabled():
            return _DISABLED
        task, allowed = self._task(request, pk, write=False)
        if not allowed:
            return Response(
                {"detail": "You do not have access to this task."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(
            {
                "task": task.pk,
                "volume": task.volume_id,
                "vocabulary": vocabulary(),
                "annotations": [
                    serialize(row) for row in annotations_for_volume(task.volume)
                ],
            }
        )

    def put(self, request, pk, label_id):
        if not instance_annotation_enabled():
            return _DISABLED
        task, allowed = self._task(request, pk, write=True)
        if not allowed:
            return Response(
                {"detail": "You cannot annotate this task."},
                status=status.HTTP_403_FORBIDDEN,
            )
        payload = request.data if isinstance(request.data, dict) else {}
        try:
            row = set_instance_annotation(
                task.volume,
                label_id,
                actor=request.user,
                # A field the client omits is left unchanged; the panel sends
                # partial updates as the user edits one control at a time.
                morphology=payload.get("morphology"),
                qa_flags=payload.get("qa_flags"),
                note=payload.get("note"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if row is None:
            # Cleared to nothing. 200 with an explicit empty shape rather than
            # 204, so the client can update its map without a second request.
            return Response(
                {
                    "label_id": int(label_id),
                    "morphology": "",
                    "qa_flags": [],
                    "note": "",
                    "review_worthy": False,
                    "updated_by": None,
                    "updated_at": None,
                }
            )
        return Response(serialize(row))

    def delete(self, request, pk, label_id):
        if not instance_annotation_enabled():
            return _DISABLED
        task, allowed = self._task(request, pk, write=True)
        if not allowed:
            return Response(
                {"detail": "You cannot annotate this task."},
                status=status.HTTP_403_FORBIDDEN,
            )
        set_instance_annotation(
            task.volume, label_id, actor=request.user,
            morphology="", qa_flags=[], note="",
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProjectInstanceSummaryView(APIView):
    """``GET /api/projects/<pk>/instance-annotations/summary/``."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if not instance_annotation_enabled():
            return _DISABLED
        project = get_object_or_404(Project, pk=pk)
        from .services import is_project_member

        if not is_project_member(request.user, project):
            return Response(
                {"detail": "You do not have access to this project."},
                status=status.HTTP_403_FORBIDDEN,
            )
        summary = project_summary(project)
        summary["vocabulary"] = vocabulary()
        return Response(summary)
