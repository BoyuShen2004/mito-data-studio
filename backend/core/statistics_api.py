"""Phase 6 HTTP surface for dashboards and statistics.

Thin by design: permission checks, serialization, CSV rendering. Every number
comes from ``core.statistics``, which knows nothing about HTTP — so the same
aggregates can be called from a management command, a test, or a future report
job without going through a view.

All endpoints are read-only and return 503 when ``FEATURE_DASHBOARDS`` is off,
matching the convention Phases 3 and 4 established: a route that disappears
with a flag makes a misconfiguration look like a 404 typo.
"""

from __future__ import annotations

import csv

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.statistics import (
    PROJECT_CSV_COLUMNS,
    annotator_statistics,
    dashboards_enabled,
    project_dashboard,
    project_dashboard_csv_row,
)
from projects.models import Project

_DISABLED = Response(
    {"detail": "Dashboards are not enabled.", "reason": "disabled"},
    status=status.HTTP_503_SERVICE_UNAVAILABLE,
)


def _may_view_project(user, project) -> bool:
    """Project statistics are visible to the project's working group.

    Reuses ``is_project_member`` rather than inventing a second rule, so
    dashboard visibility cannot drift from task visibility — and so team access
    granted in Phase 1 works here automatically when FEATURE_TEAMS is on.
    """
    from annotation.services import is_project_member

    return is_project_member(user, project)


class ProjectStatisticsView(APIView):
    """``GET /api/statistics/project/<pk>/`` — one project's dashboard."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if not dashboards_enabled():
            return _DISABLED
        project = get_object_or_404(Project, pk=pk)
        if not _may_view_project(request.user, project):
            return Response(
                {"detail": "You do not have access to this project."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(project_dashboard(project))


class ProjectStatisticsCsvView(APIView):
    """``GET /api/statistics/project/<pk>/export/`` — the same figures as CSV.

    Rendered from the identical dashboard dict the JSON endpoint returns, so a
    spreadsheet can never disagree with the screen.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if not dashboards_enabled():
            return _DISABLED
        project = get_object_or_404(Project, pk=pk)
        if not _may_view_project(request.user, project):
            return Response(
                {"detail": "You do not have access to this project."},
                status=status.HTTP_403_FORBIDDEN,
            )

        dashboard = project_dashboard(project)
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="project-{project.pk}-statistics.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(PROJECT_CSV_COLUMNS)
        writer.writerow(project_dashboard_csv_row(dashboard))
        return response


class AnnotatorStatisticsView(APIView):
    """``GET /api/statistics/annotators/`` — roster comparison, managers only.

    Cross-annotator figures are management information: an annotator may see
    their own workload through the People surfaces, but not everyone else's
    throughput. Paginated and hard-capped; see ``MAX_ANNOTATORS``.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not dashboards_enabled():
            return _DISABLED

        from accounts.roles import is_manager

        if not is_manager(request.user):
            return Response(
                {"detail": "Manager access is required for annotator statistics."},
                status=status.HTTP_403_FORBIDDEN,
            )

        project = None
        project_id = request.query_params.get("project_id")
        if project_id not in (None, "", "null"):
            project = get_object_or_404(Project, pk=project_id)

        def _int(name, default):
            raw = request.query_params.get(name)
            try:
                return int(raw) if raw not in (None, "") else default
            except (TypeError, ValueError):
                # A malformed page parameter is a bad request, not a 500 and
                # not a silent reset to page one.
                raise ValueError(name)

        try:
            limit = _int("limit", 50)
            offset = _int("offset", 0)
        except ValueError as exc:
            return Response(
                {"detail": f"{exc.args[0]} must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            annotator_statistics(project=project, limit=limit, offset=offset)
        )
