from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Count, Q
from django.utils.html import format_html

from accounts.audit import record_audit
from accounts.models import Team
from annotation.models import AnnotationTask
from annotation.services import auto_assign_project
from core.admin_common import ManagerAdminAccessMixin, count_link, lock_rows
from core.choices import AuditVerb, TaskStatus
from core.lifecycle import Lifecycle, classify_project
from volumes.models import Volume

from .models import Project
from .services import calculate_project_progress, mark_project_reviewed


class LifecycleFilter(admin.SimpleListFilter):
    """Filter projects by their New / To Proofread / Done bucket.

    Lifecycle is a rollup of the review gate and task statuses (see
    ``core.lifecycle``), so it is filtered in Python rather than via SQL.
    """

    title = "lifecycle"
    parameter_name = "lifecycle"

    def lookups(self, request, model_admin):
        return [(lc.value, lc.label) for lc in Lifecycle]

    def queryset(self, request, queryset):
        value = self.value()
        if value not in Lifecycle.values:
            return queryset
        ids = [p.pk for p in queryset if classify_project(p) == value]
        return queryset.filter(pk__in=ids)


@admin.register(Project)
class ProjectAdmin(ManagerAdminAccessMixin, admin.ModelAdmin):
    """The manager's project overview: approval, progress, and bulk operations."""

    manager_can_delete = True  # refined per-object in has_delete_permission

    list_display = (
        "title",
        "institution",
        "created_by",
        "workflow_type",
        "annotation_type",
        "status",
        "lifecycle_badge",
        "approval_state",
        "deadline",
        "volume_count",
        "task_count",
        "approved_count",
        "progress",
        "created_at",
    )
    list_filter = (
        LifecycleFilter,
        "workflow_type",
        "manager_reviewed",
        "status",
        "annotation_type",
        ("institution", admin.RelatedOnlyFieldListFilter),
        "deadline",
        "created_at",
    )
    search_fields = (
        "title",
        "dataset",
        "description",
        "institution__name",
        "created_by__username",
        "created_by__email",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("institution", "created_by", "reviewed_by")
    list_per_page = 50
    save_on_top = True
    empty_value_display = "—"
    autocomplete_fields = ("institution",)
    filter_horizontal = ("teams",)
    readonly_fields = (
        "created_by",
        "created_at",
        "manager_reviewed",
        "reviewed_by",
        "reviewed_at",
        "progress_detail",
        "related_links",
    )
    fieldsets = (
        (
            "Overview",
            {
                "fields": (
                    "title",
                    "dataset",
                    "institution",
                    "workflow_type",
                    "annotation_type",
                    "annotation_target",
                    "status",
                    "deadline",
                    "teams",
                )
            },
        ),
        (
            "Manager review",
            {
                "fields": ("manager_reviewed", "reviewed_by", "reviewed_at"),
                "description": (
                    "Requester-registered data must be approved before its "
                    "volumes can be split or assigned. Use the “Approve selected "
                    "projects” action."
                ),
            },
        ),
        ("Description & metadata", {"fields": ("description", "metadata")}),
        ("Progress", {"fields": ("progress_detail", "related_links")}),
        ("Audit", {"fields": ("created_by", "created_at")}),
    )
    actions = ("approve_projects", "auto_assign_selected", "show_progress")

    def save_related(self, request, form, formsets, change):
        before = getattr(form.instance, "_mito_team_ids_before", set())
        super().save_related(request, form, formsets, change)
        project = form.instance
        after = set(project.teams.values_list("id", flat=True))
        teams = {
            team.id: team
            for team in Team.objects.filter(id__in=before | after)
        }
        for team_id in sorted(after - before):
            team = teams[team_id]
            record_audit(
                request.user,
                AuditVerb.PROJECT_TEAM_GRANTED,
                project,
                team_id=team.id,
                team_name=team.name,
            )
        for team_id in sorted(before - after):
            team = teams[team_id]
            record_audit(
                request.user,
                AuditVerb.PROJECT_TEAM_REVOKED,
                project,
                team_id=team.id,
                team_name=team.name,
            )

    # --- query optimisation -------------------------------------------------
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _volumes=Count("volumes", distinct=True),
            _tasks=Count("tasks", distinct=True),
            _approved=Count(
                "tasks",
                filter=Q(tasks__status=TaskStatus.APPROVED),
                distinct=True,
            ),
        )

    # --- computed columns ---------------------------------------------------
    @admin.display(description="Lifecycle")
    def lifecycle_badge(self, obj):
        return Lifecycle(classify_project(obj)).label

    @admin.display(description="Approved", boolean=True, ordering="manager_reviewed")
    def approval_state(self, obj):
        return obj.manager_reviewed

    @admin.display(description="Volumes", ordering="_volumes")
    def volume_count(self, obj):
        return obj._volumes

    @admin.display(description="Tasks", ordering="_tasks")
    def task_count(self, obj):
        return obj._tasks

    @admin.display(description="Approved tasks", ordering="_approved")
    def approved_count(self, obj):
        return obj._approved

    @admin.display(description="Progress")
    def progress(self, obj):
        total = obj._tasks or 0
        pct = round(100 * (obj._approved or 0) / total, 1) if total else 0.0
        return f"{pct}%"

    @admin.display(description="Progress detail")
    def progress_detail(self, obj):
        if not obj.pk:
            return "—"
        p = calculate_project_progress(obj)
        return (
            f"{p['approved_tasks']}/{p['total_tasks']} tasks approved "
            f"({p['percent_complete']}%) across {p['volumes']} volume(s)."
        )

    @admin.display(description="Related")
    def related_links(self, obj):
        if not obj.pk:
            return "—"
        volumes = count_link(
            Volume, f"{obj.volumes.count()} volumes", project__id__exact=obj.pk
        )
        tasks = count_link(
            AnnotationTask, f"{obj.tasks.count()} tasks", project__id__exact=obj.pk
        )
        return format_html("{} · {}", volumes, tasks)

    # --- lifecycle ----------------------------------------------------------
    def save_model(self, request, obj, form, change):
        # save_related() runs after this save and applies the M2M form. Preserve
        # the previous grants on this request's instance (not the singleton
        # ModelAdmin), so concurrent manager saves cannot cross-contaminate.
        obj._mito_team_ids_before = (  # noqa: SLF001
            set(obj.teams.values_list("id", flat=True)) if change else set()
        )
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        # A project a manager creates in the admin is approved on creation.
        if not change and not obj.manager_reviewed:
            mark_project_reviewed(obj, reviewer=request.user)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if not self._is_manager(request):
            return False
        # Managers may not delete a project that already has downstream tasks.
        if obj is not None and obj.tasks.exists():
            return False
        return True

    # --- actions ------------------------------------------------------------
    @admin.action(description="Approve selected projects")
    def approve_projects(self, request, queryset):
        approved = 0
        already = 0
        with transaction.atomic():
            for project in lock_rows(queryset):
                if project.manager_reviewed:
                    already += 1
                    continue
                mark_project_reviewed(project, reviewer=request.user)
                approved += 1
        if approved:
            self.message_user(
                request, f"Approved {approved} project(s).", messages.SUCCESS
            )
        if already:
            self.message_user(
                request,
                f"{already} project(s) were already approved.",
                messages.WARNING,
            )

    @admin.action(description="Auto-assign tasks for selected approved projects")
    def auto_assign_selected(self, request, queryset):
        assigned = 0
        created = 0
        skipped_volumes = 0
        not_reviewed = []
        for project in queryset:
            if not project.manager_reviewed:
                not_reviewed.append(project.title)
                continue
            summary = auto_assign_project(project)
            assigned += summary.get("assigned", 0)
            created += summary.get("created_tasks", 0)
            skipped_volumes += summary.get("skipped_volumes", 0)
        if assigned or created:
            self.message_user(
                request,
                f"Created {created} volume task(s) and assigned {assigned} "
                f"task(s) across the selected projects.",
                messages.SUCCESS,
            )
        elif not not_reviewed:
            self.message_user(
                request,
                "Nothing to assign (no unassigned tasks or no active annotators).",
                messages.WARNING,
            )
        if skipped_volumes:
            self.message_user(
                request,
                f"{skipped_volumes} volume(s) skipped: no detectable shape yet.",
                messages.WARNING,
            )
        if not_reviewed:
            self.message_user(
                request,
                "Skipped (approve first): " + ", ".join(not_reviewed),
                messages.ERROR,
            )

    @admin.action(description="Show progress for selected projects")
    def show_progress(self, request, queryset):
        for project in queryset:
            p = calculate_project_progress(project)
            self.message_user(
                request,
                f"{project.title}: {p['approved_tasks']}/{p['total_tasks']} "
                f"approved ({p['percent_complete']}%).",
                messages.INFO,
            )
