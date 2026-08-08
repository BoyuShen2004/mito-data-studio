"""Manager Admin for ProcessingJob.

Managers monitor jobs, retry failed ones, cancel active ones, and (in dev) run
a dispatch pass on demand. Every action calls the ``processing.services`` layer;
no job state is mutated inline here.
"""

import json

from django.contrib import admin, messages
from django.utils.html import format_html

from core.admin_common import ManagerAdminAccessMixin, NumericIdSearchMixin, admin_link
from core.choices import ACTIVE_JOB_STATUSES, TERMINAL_JOB_STATUSES

from .models import ProcessingJob
from .services import cancel_job, dispatch_job, poll_job, retry_job


@admin.register(ProcessingJob)
class ProcessingJobAdmin(
    ManagerAdminAccessMixin, NumericIdSearchMixin, admin.ModelAdmin
):
    manager_can_add = False  # jobs are created by the service layer, not by hand

    list_display = (
        "id",
        "job_type",
        "backend",
        "status",
        "project_link",
        "volume",
        "task",
        "external_job_id",
        "retry_count",
        "created_at",
        "finished_at",
    )
    list_display_links = ("id",)
    list_filter = ("status", "job_type", "backend", "created_at")
    search_fields = (
        "external_job_id",
        "project__title",
        "volume__name",
        "error_message",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("project", "volume", "task")
    list_per_page = 50
    empty_value_display = "—"
    readonly_fields = (
        "job_type",
        "backend",
        "status",
        "project",
        "volume",
        "task",
        "external_job_id",
        "config_display",
        "input_paths_display",
        "output_paths_display",
        "log_path",
        "error_message",
        "retry_count",
        "created_by",
        "created_at",
        "submitted_at",
        "started_at",
        "finished_at",
    )
    exclude = ("config", "input_paths", "output_paths")
    actions = ("retry_selected", "cancel_selected", "run_now_selected")

    @admin.display(description="Project")
    def project_link(self, obj):
        return admin_link(obj.project, obj.project.title if obj.project else None)

    @admin.display(description="Config")
    def config_display(self, obj):
        return self._json(obj.config)

    @admin.display(description="Input paths")
    def input_paths_display(self, obj):
        return self._json(obj.input_paths)

    @admin.display(description="Output paths")
    def output_paths_display(self, obj):
        return self._json(obj.output_paths)

    @staticmethod
    def _json(value):
        if not value:
            return "—"
        return format_html(
            '<pre style="max-height:22em;overflow:auto;margin:0">{}</pre>',
            json.dumps(value, indent=2, sort_keys=True),
        )

    # --- actions -----------------------------------------------------------
    @admin.action(description="Retry selected jobs (failed / cancelled)")
    def retry_selected(self, request, queryset):
        done, skipped = 0, 0
        for job in queryset:
            if job.status in TERMINAL_JOB_STATUSES:
                retry_job(job)
                done += 1
            else:
                skipped += 1
        if done:
            self.message_user(request, f"Requeued {done} job(s).", messages.SUCCESS)
        if skipped:
            self.message_user(
                request,
                f"{skipped} job(s) skipped (not in a terminal state).",
                messages.WARNING,
            )

    @admin.action(description="Cancel selected jobs")
    def cancel_selected(self, request, queryset):
        done = 0
        for job in queryset:
            if job.status in ACTIVE_JOB_STATUSES or job.status == "queued":
                cancel_job(job)
                done += 1
        self.message_user(request, f"Cancelled {done} job(s).", messages.SUCCESS)

    @admin.action(description="Run selected queued jobs now (dev)")
    def run_now_selected(self, request, queryset):
        done = 0
        for job in queryset.filter(status="queued"):
            job.status = "submitted"
            job.save(update_fields=["status"])
            dispatch_job(job)
            done += 1
        # Also poll anything already active.
        for job in queryset.filter(status__in=ACTIVE_JOB_STATUSES):
            poll_job(job)
        self.message_user(request, f"Dispatched {done} job(s).", messages.SUCCESS)
