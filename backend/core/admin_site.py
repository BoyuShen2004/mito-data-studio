"""The Manager Admin site.

Django Admin is the primary operational interface for the Manager role, so the
site is branded accordingly and gated to managers (superusers included). The
index is augmented with an operational dashboard whose metrics link to the
relevant filtered changelists.
"""

from __future__ import annotations

from django.contrib.admin import AdminSite
from django.utils import timezone

from accounts.roles import is_manager

from .admin_common import changelist_url


class ManagerAdminSite(AdminSite):
    site_header = "Mito Data Agent Manager"
    site_title = "Mito Data Agent Manager"
    index_title = "Manager operations"
    index_template = "admin/manager_index.html"

    def has_permission(self, request):
        """Only active managers (and superusers) may use the Manager Admin."""
        user = request.user
        return bool(user and user.is_active and user.is_staff and is_manager(user))

    def index(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["dashboard_metrics"] = self._dashboard_metrics()
        extra_context["lifecycle_metrics"] = self._lifecycle_metrics()
        return super().index(request, extra_context)

    @staticmethod
    def _lifecycle_metrics():
        """New / To Proofread / Done project counts for the dashboard."""
        from core.lifecycle import Lifecycle, project_lifecycle_counts
        from projects.models import Project

        counts = project_lifecycle_counts(
            Project.objects.all().only(
                "id", "status", "manager_reviewed"
            ).prefetch_related("tasks")
        )
        # Each bucket links to the Project changelist filtered by lifecycle.
        return [
            {
                "label": Lifecycle(bucket).label,
                "value": counts.get(bucket, 0),
                "url": changelist_url(Project, lifecycle=bucket),
            }
            for bucket in Lifecycle.values
        ]

    @staticmethod
    def _dashboard_metrics():
        # Imported lazily so the site class can be referenced during app setup.
        from django.db.models import Count, Q

        from accounts.models import AnnotatorProfile
        from annotation.models import AnnotationSubmission, AnnotationTask
        from core.choices import ACTIVE_TASK_STATUSES, TaskStatus
        from projects.models import Project

        today = timezone.now().date()

        awaiting_approval = Project.objects.filter(manager_reviewed=False).count()
        approved_projects = Project.objects.filter(manager_reviewed=True).count()
        unassigned = AnnotationTask.objects.filter(
            status=TaskStatus.UNASSIGNED
        ).count()
        active_tasks = AnnotationTask.objects.filter(
            status__in=ACTIVE_TASK_STATUSES
        ).count()
        revision = AnnotationTask.objects.filter(
            status=TaskStatus.REVISION_REQUESTED
        ).count()
        overdue = (
            AnnotationTask.objects.filter(deadline__lt=today)
            .exclude(status=TaskStatus.APPROVED)
            .count()
        )
        awaiting_review = AnnotationSubmission.objects.filter(
            task__status=TaskStatus.SUBMITTED
        ).count()

        profiles = AnnotatorProfile.objects.filter(
            is_active_annotator=True
        ).annotate(
            active=Count(
                "user__annotation_tasks",
                filter=Q(user__annotation_tasks__status__in=ACTIVE_TASK_STATUSES),
            )
        )
        active_annotators = len(profiles)
        at_capacity = sum(1 for p in profiles if p.active >= p.max_active_tasks)

        def metric(label, value, url, warn=False):
            return {"label": label, "value": value, "url": url, "warn": warn}

        return [
            metric(
                "Projects awaiting approval",
                awaiting_approval,
                changelist_url(Project, manager_reviewed__exact=0),
                warn=awaiting_approval > 0,
            ),
            metric(
                "Approved projects",
                approved_projects,
                changelist_url(Project, manager_reviewed__exact=1),
            ),
            metric(
                "Unassigned tasks",
                unassigned,
                changelist_url(AnnotationTask, status__exact=TaskStatus.UNASSIGNED),
                warn=unassigned > 0,
            ),
            metric(
                "Assigned / in-progress tasks",
                active_tasks,
                changelist_url(AnnotationTask, active="1"),
            ),
            metric(
                "Submissions awaiting review",
                awaiting_review,
                changelist_url(AnnotationSubmission, review_state="pending"),
                warn=awaiting_review > 0,
            ),
            metric(
                "Revision-requested tasks",
                revision,
                changelist_url(
                    AnnotationTask, status__exact=TaskStatus.REVISION_REQUESTED
                ),
                warn=revision > 0,
            ),
            metric(
                "Overdue tasks",
                overdue,
                changelist_url(AnnotationTask, overdue="1"),
                warn=overdue > 0,
            ),
            metric(
                "Active annotators",
                active_annotators,
                changelist_url(AnnotatorProfile, is_active_annotator__exact=1),
            ),
            metric(
                "Annotators at capacity",
                at_capacity,
                changelist_url(AnnotatorProfile, capacity="full"),
                warn=at_capacity > 0,
            ),
        ]
