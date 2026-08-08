from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Count, Q

from annotation.models import AnnotationTask
from annotation.services import create_whole_volume_task
from core.admin_common import ManagerAdminAccessMixin, admin_link, count_link
from core.choices import ACTIVE_TASK_STATUSES, TaskStatus

from .models import Volume


@admin.register(Volume)
class VolumeAdmin(ManagerAdminAccessMixin, admin.ModelAdmin):
    """Volumes: inspect scientific/file metadata and turn them into tasks."""

    list_display = (
        "name",
        "project_link",
        "image_path",
        "label_available",
        "label_type",
        "file_format",
        "shape_display",
        "task_count",
        "assigned_count",
        "completed_count",
        "status",
        "created_at",
    )
    list_filter = (
        ("project", admin.RelatedOnlyFieldListFilter),
        "label_type",
        "file_format",
        "status",
        "created_at",
    )
    search_fields = (
        "name",
        "project__title",
        "image_path",
        "label_path",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("project",)
    list_per_page = 50
    save_on_top = True
    empty_value_display = "—"
    autocomplete_fields = ("project",)
    readonly_fields = (
        "image_path",
        "label_path",
        "image_location",
        "label_location",
        "shape_z",
        "shape_y",
        "shape_x",
        "voxel_size_z",
        "voxel_size_y",
        "voxel_size_x",
        "metadata",
        "created_at",
        "related_links",
    )
    fieldsets = (
        (
            "Identity",
            {"fields": ("project", "name", "status")},
        ),
        (
            "Files (registered HPC references)",
            {
                "fields": (
                    "image_path",
                    "image_location",
                    "label_path",
                    "label_location",
                    "label_type",
                    "file_format",
                ),
                "description": "Paths are set at registration and are read-only.",
            },
        ),
        (
            "Derived scientific metadata",
            {
                "fields": (
                    ("shape_z", "shape_y", "shape_x"),
                    ("voxel_size_z", "voxel_size_y", "voxel_size_x"),
                    "metadata",
                ),
                "description": "Derived from the files; not manually editable.",
            },
        ),
        ("Related", {"fields": ("related_links",)}),
    )
    actions = ("create_whole_volume_tasks",)

    # --- query optimisation -------------------------------------------------
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _tasks=Count("tasks", distinct=True),
            _assigned=Count(
                "tasks",
                filter=Q(tasks__status__in=ACTIVE_TASK_STATUSES),
                distinct=True,
            ),
            _completed=Count(
                "tasks",
                filter=Q(tasks__status=TaskStatus.APPROVED),
                distinct=True,
            ),
        )

    # --- computed columns ---------------------------------------------------
    @admin.display(description="Project")
    def project_link(self, obj):
        return admin_link(obj.project)

    @admin.display(description="Label", boolean=True)
    def label_available(self, obj):
        return obj.has_label

    @admin.display(description="Shape (z,y,x)")
    def shape_display(self, obj):
        return f"{obj.shape_z or '?'},{obj.shape_y or '?'},{obj.shape_x or '?'}"

    @admin.display(description="Tasks", ordering="_tasks")
    def task_count(self, obj):
        return obj._tasks

    @admin.display(description="Active", ordering="_assigned")
    def assigned_count(self, obj):
        return obj._assigned

    @admin.display(description="Completed", ordering="_completed")
    def completed_count(self, obj):
        return obj._completed

    @admin.display(description="Related")
    def related_links(self, obj):
        if not obj.pk:
            return "—"
        return count_link(
            AnnotationTask, f"{obj.tasks.count()} tasks", volume__id__exact=obj.pk
        )

    # --- actions ------------------------------------------------------------
    def _eligibility_error(self, volume):
        """Return a per-volume reason it cannot be turned into tasks, or None."""
        if not volume.project.manager_reviewed:
            return "project not approved"
        if volume.tasks.exists():
            return "already has tasks"
        if not volume.shape_z:
            return "no detectable shape"
        return None

    @admin.action(description="Create one whole-volume task per selected volume")
    def create_whole_volume_tasks(self, request, queryset):
        created = 0
        skipped = []
        for volume in queryset.select_related("project"):
            reason = self._eligibility_error(volume)
            if reason:
                skipped.append(f"{volume.name} ({reason})")
                continue
            with transaction.atomic():
                task = create_whole_volume_task(volume)
            if task is not None:
                created += 1
            else:
                skipped.append(f"{volume.name} (not eligible)")
        if created:
            self.message_user(
                request,
                f"Created {created} whole-volume task(s).",
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request, "Skipped: " + "; ".join(skipped), messages.WARNING
            )
