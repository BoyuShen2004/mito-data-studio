import json

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, OuterRef, Subquery
from django.shortcuts import render
from django.utils import timezone
from django.utils.html import format_html

from core.admin_common import (
    ManagerAdminAccessMixin,
    NumericIdSearchMixin,
    admin_link,
    count_link,
    lock_rows,
)
from core.choices import ACTIVE_TASK_STATUSES, ReviewDecision, TaskStatus
from projects.models import Project

from .models import AnnotationSubmission, AnnotationTask, ReviewRecord
from .services import assign_task_to_annotator, review_submission

User = get_user_model()


# --- custom list filters ---------------------------------------------------

class ActiveTaskFilter(admin.SimpleListFilter):
    title = "active work"
    parameter_name = "active"

    def lookups(self, request, model_admin):
        return (("1", "Assigned / in progress"),)

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(status__in=ACTIVE_TASK_STATUSES)
        return queryset


class OverdueTaskFilter(admin.SimpleListFilter):
    title = "overdue"
    parameter_name = "overdue"

    def lookups(self, request, model_admin):
        return (("1", "Past deadline, not approved"),)

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(
                deadline__lt=timezone.now().date()
            ).exclude(status=TaskStatus.APPROVED)
        return queryset


class ReviewStateFilter(admin.SimpleListFilter):
    title = "review state"
    parameter_name = "review_state"

    def lookups(self, request, model_admin):
        return (("pending", "Awaiting review"), ("reviewed", "Reviewed"))

    def queryset(self, request, queryset):
        if self.value() == "pending":
            return queryset.filter(task__status=TaskStatus.SUBMITTED)
        if self.value() == "reviewed":
            return queryset.exclude(reviews__isnull=True).distinct()
        return queryset


# --- intermediate-action forms ---------------------------------------------

def _active_annotator_queryset():
    return User.objects.filter(
        annotator_profile__is_active_annotator=True
    ).order_by("username")


class AssignAnnotatorForm(forms.Form):
    annotator = forms.ModelChoiceField(
        queryset=_active_annotator_queryset(),
        label="Assign to annotator",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Refresh the queryset per request (avoids stale evaluation at import).
        self.fields["annotator"].queryset = _active_annotator_queryset()


class ReviewCommentForm(forms.Form):
    comments = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        required=True,
        help_text="Explain the decision for the annotator and the audit record.",
    )


# --- task admin -------------------------------------------------------------

@admin.register(AnnotationTask)
class AnnotationTaskAdmin(
    ManagerAdminAccessMixin, NumericIdSearchMixin, admin.ModelAdmin
):
    """The manager's task operations screen: monitor, assign, and reassign."""

    list_display = (
        "id",
        "project_link",
        "volume",
        "frame_range",
        "task_type",
        "status",
        "assigned_to",
        "priority",
        "difficulty",
        "deadline",
        "submission_count",
        "latest_qc",
        "assigned_at",
        "submitted_at",
        "approved_at",
    )
    list_display_links = ("id",)
    list_filter = (
        "status",
        "task_type",
        ("project", admin.RelatedOnlyFieldListFilter),
        ("assigned_to", admin.RelatedOnlyFieldListFilter),
        "priority",
        "difficulty",
        "deadline",
        ActiveTaskFilter,
        OverdueTaskFilter,
    )
    search_fields = (
        "project__title",
        "volume__name",
        "assigned_to__username",
        "assigned_to__email",
    )
    date_hierarchy = "created_at"
    ordering = ("-priority", "created_at")
    list_select_related = ("project", "volume", "assigned_to")
    list_per_page = 50
    save_on_top = True
    empty_value_display = "—"
    autocomplete_fields = ("project", "volume")
    actions = (
        "assign_to_annotator",
        "unassign_tasks",
        "increase_priority",
        "decrease_priority",
    )
    fieldsets = (
        ("Assignment", {"fields": ("assigned_to", "status")}),
        (
            "Spatial / frame range",
            {
                "fields": (
                    "volume",
                    ("z_start", "z_end"),
                    ("y_start", "y_end"),
                    ("x_start", "x_end"),
                )
            },
        ),
        ("Workflow", {"fields": ("project", "task_type", "priority", "difficulty")}),
        ("Instructions & deadline", {"fields": ("instructions", "deadline")}),
        (
            "Timestamps",
            {"fields": ("created_at", "assigned_at", "submitted_at", "approved_at")},
        ),
        ("Related", {"fields": ("submission_links",)}),
    )

    # Status/assignment/lifecycle move only through services, not free edits.
    base_readonly = (
        "assigned_to",
        "status",
        "created_at",
        "assigned_at",
        "submitted_at",
        "approved_at",
        "submission_links",
    )

    def get_readonly_fields(self, request, obj=None):
        fields = list(self.base_readonly)
        if obj is not None:  # project/volume are fixed once a task exists
            fields += ["project", "volume"]
        return tuple(fields)

    # --- query optimisation -------------------------------------------------
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        latest_qc = (
            AnnotationSubmission.objects.filter(task=OuterRef("pk"))
            .order_by("-submitted_at")
            .values("qc_status")[:1]
        )
        return qs.annotate(
            _subs=Count("submissions", distinct=True),
            _latest_qc=Subquery(latest_qc),
        )

    # --- computed columns ---------------------------------------------------
    @admin.display(description="Project")
    def project_link(self, obj):
        return admin_link(obj.project, obj.project.title if obj.project else None)

    @admin.display(description="Frames (z)")
    def frame_range(self, obj):
        return obj.frame_label

    @admin.display(description="Submissions", ordering="_subs")
    def submission_count(self, obj):
        return obj._subs

    @admin.display(description="Latest QC")
    def latest_qc(self, obj):
        return obj._latest_qc or "—"

    @admin.display(description="Submissions")
    def submission_links(self, obj):
        if not obj.pk:
            return "—"
        return count_link(
            AnnotationSubmission,
            f"{obj.submissions.count()} submission(s)",
            task__id__exact=obj.pk,
        )

    # --- validation ---------------------------------------------------------
    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        class ValidatedForm(form):
            def clean(self_inner):
                cleaned = super().clean()
                pairs = (
                    ("z_start", "z_end"),
                    ("y_start", "y_end"),
                    ("x_start", "x_end"),
                )
                for start, end in pairs:
                    s, e = cleaned.get(start), cleaned.get(end)
                    if s is not None and e is not None and e <= s:
                        self_inner.add_error(
                            end, f"{end} must be greater than {start}."
                        )
                return cleaned

        return ValidatedForm

    # --- actions ------------------------------------------------------------
    @admin.action(description="Assign selected tasks to an annotator")
    def assign_to_annotator(self, request, queryset):
        if "apply" in request.POST:
            form = AssignAnnotatorForm(request.POST)
            if form.is_valid():
                annotator = form.cleaned_data["annotator"]
                self._do_assign(request, queryset, annotator)
                return None
        else:
            form = AssignAnnotatorForm()
        return render(
            request,
            "admin/annotation/assign_tasks.html",
            {
                **self.admin_site.each_context(request),
                "title": "Assign tasks to an annotator",
                "tasks": queryset,
                "form": form,
                "action": "assign_to_annotator",
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
                "selected": queryset.values_list("pk", flat=True),
                "opts": self.model._meta,
            },
        )

    def _do_assign(self, request, queryset, annotator):
        profile = getattr(annotator, "annotator_profile", None)
        max_active = profile.max_active_tasks if profile else 0
        current = AnnotationTask.objects.filter(
            assigned_to=annotator, status__in=ACTIVE_TASK_STATUSES
        ).count()
        remaining = max(max_active - current, 0)

        assigned = 0
        over_capacity = 0
        with transaction.atomic():
            for task in lock_rows(queryset):
                if task.assigned_to_id == annotator.id:
                    continue
                if remaining <= 0:
                    over_capacity += 1
                    continue
                assign_task_to_annotator(task, annotator=annotator)
                remaining -= 1
                assigned += 1
        if assigned:
            self.message_user(
                request,
                f"Assigned {assigned} task(s) to {annotator.get_username()}.",
                messages.SUCCESS,
            )
        if over_capacity:
            self.message_user(
                request,
                f"{over_capacity} task(s) not assigned: "
                f"{annotator.get_username()} is at max_active_tasks "
                f"({max_active}).",
                messages.WARNING,
            )
        if not assigned and not over_capacity:
            self.message_user(
                request, "No tasks needed reassignment.", messages.INFO
            )

    @admin.action(description="Unassign selected tasks (assigned / in progress)")
    def unassign_tasks(self, request, queryset):
        eligible = queryset.filter(status__in=ACTIVE_TASK_STATUSES)
        count = 0
        with transaction.atomic():
            for task in lock_rows(eligible):
                assign_task_to_annotator(task, annotator=None)
                count += 1
        skipped = queryset.count() - count
        if count:
            self.message_user(
                request, f"Unassigned {count} task(s).", messages.SUCCESS
            )
        if skipped:
            self.message_user(
                request,
                f"{skipped} task(s) skipped (not in an assignable state).",
                messages.WARNING,
            )

    def _bump_priority(self, request, queryset, delta):
        updated = 0
        for task in queryset:
            task.priority = max(task.priority + delta, 0)
            task.save(update_fields=["priority"])
            updated += 1
        self.message_user(
            request,
            f"Adjusted priority on {updated} task(s) by {delta:+d}.",
            messages.SUCCESS,
        )

    @admin.action(description="Increase priority (+1)")
    def increase_priority(self, request, queryset):
        self._bump_priority(request, queryset, 1)

    @admin.action(description="Decrease priority (−1)")
    def decrease_priority(self, request, queryset):
        self._bump_priority(request, queryset, -1)


# --- submission admin -------------------------------------------------------

@admin.register(AnnotationSubmission)
class AnnotationSubmissionAdmin(
    ManagerAdminAccessMixin, NumericIdSearchMixin, admin.ModelAdmin
):
    """Review annotator submissions: inspect QC and approve/reject/revise."""

    manager_can_add = False  # submissions come from annotators via the app

    list_display = (
        "id",
        "task_link",
        "project",
        "volume",
        "annotator",
        "source",
        "qc_status",
        "submitted_at",
        "latest_decision",
        "review_count",
    )
    list_display_links = ("id",)
    list_filter = (
        "source",
        "qc_status",
        ReviewStateFilter,
        ("task__project", admin.RelatedOnlyFieldListFilter),
        ("annotator", admin.RelatedOnlyFieldListFilter),
        "submitted_at",
    )
    search_fields = (
        "task__project__title",
        "task__volume__name",
        "annotator__username",
        "annotator__email",
    )
    id_search_field = "task__id"
    date_hierarchy = "submitted_at"
    ordering = ("-submitted_at",)
    list_select_related = (
        "task",
        "task__project",
        "task__volume",
        "annotator",
    )
    list_per_page = 50
    save_on_top = True
    empty_value_display = "—"
    autocomplete_fields = ("task",)
    readonly_fields = (
        "task",
        "annotator",
        "source",
        "qc_status",
        "qc_report_display",
        "label_file_link",
        "submitted_at",
        "related_links",
    )
    exclude = ("qc_report", "label_file", "notes")
    actions = ("approve_selected", "reject_selected", "request_revision_selected")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _reviews=Count("reviews", distinct=True)
        )

    # --- computed columns ---------------------------------------------------
    @admin.display(description="Task")
    def task_link(self, obj):
        return admin_link(obj.task, f"Task #{obj.task_id}")

    @admin.display(description="Project")
    def project(self, obj):
        return obj.task.project

    @admin.display(description="Volume")
    def volume(self, obj):
        return obj.task.volume

    @admin.display(description="Reviews", ordering="_reviews")
    def review_count(self, obj):
        return obj._reviews

    @admin.display(description="Latest decision")
    def latest_decision(self, obj):
        review = obj.reviews.first()  # ReviewRecord default ordering: -reviewed_at
        return review.get_decision_display() if review else "—"

    @admin.display(description="QC report")
    def qc_report_display(self, obj):
        if not obj.qc_report:
            return "—"
        # Escape the JSON string; format_html never marks user content safe.
        return format_html(
            '<pre style="max-height:22em;overflow:auto;margin:0">{}</pre>',
            json.dumps(obj.qc_report, indent=2, sort_keys=True),
        )

    @admin.display(description="Label file")
    def label_file_link(self, obj):
        field = obj.label_file
        if not field:
            return "—"
        try:
            url = field.url
        except (ValueError, NotImplementedError):
            return field.name
        return format_html('<a href="{}">{}</a>', url, field.name)

    @admin.display(description="Related")
    def related_links(self, obj):
        if not obj.pk:
            return "—"
        return count_link(
            ReviewRecord,
            f"{obj.reviews.count()} review(s)",
            submission__id__exact=obj.pk,
        )

    # --- review actions -----------------------------------------------------
    def _reviewable(self, submission):
        return submission.task.status == TaskStatus.SUBMITTED

    @admin.action(description="Approve selected submissions")
    def approve_selected(self, request, queryset):
        done = 0
        skipped = 0
        with transaction.atomic():
            for submission in queryset:
                if not self._reviewable(submission):
                    skipped += 1
                    continue
                review_submission(
                    submission=submission,
                    reviewer=request.user,
                    decision=ReviewDecision.APPROVED,
                    comments="",
                )
                done += 1
        if done:
            self.message_user(
                request, f"Approved {done} submission(s).", messages.SUCCESS
            )
        if skipped:
            self.message_user(
                request,
                f"{skipped} submission(s) skipped (task not awaiting review).",
                messages.WARNING,
            )

    def _review_with_comments(self, request, queryset, decision, verb):
        if "apply" in request.POST:
            form = ReviewCommentForm(request.POST)
            if form.is_valid():
                comments = form.cleaned_data["comments"]
                done = 0
                skipped = 0
                with transaction.atomic():
                    for submission in queryset:
                        if not self._reviewable(submission):
                            skipped += 1
                            continue
                        review_submission(
                            submission=submission,
                            reviewer=request.user,
                            decision=decision,
                            comments=comments,
                        )
                        done += 1
                if done:
                    self.message_user(
                        request, f"{verb} {done} submission(s).", messages.SUCCESS
                    )
                if skipped:
                    self.message_user(
                        request,
                        f"{skipped} submission(s) skipped (task not awaiting "
                        "review).",
                        messages.WARNING,
                    )
                return None
        else:
            form = ReviewCommentForm()
        return render(
            request,
            "admin/annotation/review_submissions.html",
            {
                **self.admin_site.each_context(request),
                "title": f"{verb} submissions",
                "verb": verb,
                "submissions": queryset,
                "form": form,
                "action": (
                    "reject_selected"
                    if decision == ReviewDecision.REJECTED
                    else "request_revision_selected"
                ),
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
                "selected": queryset.values_list("pk", flat=True),
                "opts": self.model._meta,
            },
        )

    @admin.action(description="Reject selected submissions (with comments)")
    def reject_selected(self, request, queryset):
        return self._review_with_comments(
            request, queryset, ReviewDecision.REJECTED, "Rejected"
        )

    @admin.action(description="Request revision on selected (with comments)")
    def request_revision_selected(self, request, queryset):
        return self._review_with_comments(
            request, queryset, ReviewDecision.REVISION_REQUESTED, "Requested revision on"
        )


# --- review record admin (audit history) -----------------------------------

@admin.register(ReviewRecord)
class ReviewRecordAdmin(
    ManagerAdminAccessMixin, NumericIdSearchMixin, admin.ModelAdmin
):
    """Immutable review history. Managers inspect; only superusers may delete."""

    manager_can_add = False
    manager_can_change = False  # audit records are not editable

    list_display = (
        "id",
        "project",
        "task",
        "submission_link",
        "reviewer",
        "decision",
        "short_comments",
        "reviewed_at",
    )
    list_display_links = ("id",)
    list_filter = (
        "decision",
        ("reviewer", admin.RelatedOnlyFieldListFilter),
        "reviewed_at",
    )
    search_fields = (
        "submission__task__project__title",
        "reviewer__username",
        "reviewer__email",
        "comments",
    )
    id_search_field = "submission__task__id"
    date_hierarchy = "reviewed_at"
    ordering = ("-reviewed_at",)
    list_select_related = (
        "submission",
        "submission__task",
        "submission__task__project",
        "reviewer",
    )
    list_per_page = 50
    empty_value_display = "—"

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    @admin.display(description="Project")
    def project(self, obj):
        return obj.submission.task.project

    @admin.display(description="Task")
    def task(self, obj):
        return admin_link(obj.submission.task, f"Task #{obj.submission.task_id}")

    @admin.display(description="Submission")
    def submission_link(self, obj):
        return admin_link(obj.submission, f"Submission #{obj.submission_id}")

    @admin.display(description="Comments")
    def short_comments(self, obj):
        text = obj.comments or ""
        return (text[:60] + "…") if len(text) > 60 else (text or "—")
