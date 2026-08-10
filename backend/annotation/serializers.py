from rest_framework import serializers

from .models import AnnotationSubmission, AnnotationTask, HardCase, ReviewRecord


class AnnotationTaskSerializer(serializers.ModelSerializer):
    project_title = serializers.CharField(source="project.title", read_only=True)
    dataset = serializers.SerializerMethodField()
    # The biomedical metadata every role sees: it lives on the dataset (that is
    # where registration records it), so managers, requesters, and annotators
    # all read the same source rather than the near-empty project.metadata.
    dataset_metadata = serializers.SerializerMethodField()
    # Volume-derived facts, surfaced so annotators see the scanned resolution.
    voxel_size_z = serializers.FloatField(source="volume.voxel_size_z", read_only=True)
    voxel_size_y = serializers.FloatField(source="volume.voxel_size_y", read_only=True)
    voxel_size_x = serializers.FloatField(source="volume.voxel_size_x", read_only=True)
    shape_z = serializers.IntegerField(source="volume.shape_z", read_only=True)
    shape_y = serializers.IntegerField(source="volume.shape_y", read_only=True)
    shape_x = serializers.IntegerField(source="volume.shape_x", read_only=True)
    file_format = serializers.CharField(source="volume.file_format", read_only=True)
    volume_status = serializers.CharField(source="volume.status", read_only=True)
    ready_streaming = serializers.BooleanField(source="volume.ready_streaming", read_only=True)
    streaming_status = serializers.CharField(source="volume.streaming_status", read_only=True)
    streaming_error = serializers.CharField(source="volume.streaming_error", read_only=True)
    region_ready_streaming = serializers.BooleanField(source="volume.region_ready_streaming", read_only=True)
    region_streaming_status = serializers.CharField(source="volume.region_streaming_status", read_only=True)
    region_streaming_error = serializers.CharField(source="volume.region_streaming_error", read_only=True)
    has_region_mask = serializers.BooleanField(source="volume.has_region_mask", read_only=True)
    volume_name = serializers.CharField(source="volume.name", read_only=True)
    image_location = serializers.CharField(
        source="volume.image_location", read_only=True
    )
    region_mask_location = serializers.CharField(
        source="volume.region_mask_location", read_only=True
    )
    region_mask_coverage = serializers.FloatField(
        source="volume.region_mask_coverage", read_only=True, allow_null=True
    )
    label_location = serializers.CharField(
        source="volume.label_location", read_only=True
    )
    # none / partial / prediction — same vocabulary as volume registration.
    label_type = serializers.CharField(source="volume.label_type", read_only=True)
    assigned_to_username = serializers.CharField(
        source="assigned_to.username", read_only=True, default=""
    )
    frame_label = serializers.CharField(read_only=True)
    last_decision_by_username = serializers.CharField(
        source="last_decision_by.username", read_only=True, default=""
    )
    # API-driven Submit/Annotate gating. The frontend must read these rather
    # than re-deriving from ``status`` — a hard-coded status list on the client
    # is exactly what made "Submit for review" disappear after the first
    # submit (see annotation.services.can_submit_task).
    can_submit = serializers.SerializerMethodField()
    can_annotate = serializers.SerializerMethodField()
    review_history = serializers.SerializerMethodField()

    def get_dataset_metadata(self, obj) -> dict:
        dataset = getattr(obj.volume, "dataset", None) if obj.volume_id else None
        return dataset.metadata if dataset and dataset.metadata else {}

    def get_dataset(self, obj) -> str:
        dataset = getattr(obj.volume, "dataset", None) if obj.volume_id else None
        return dataset.name if dataset else (obj.project.dataset or "")

    def _user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def get_can_submit(self, obj) -> bool:
        from .services import can_submit_task

        user = self._user()
        # No request in context (a serializer instantiated for a non-HTTP
        # caller) means we cannot know who is asking — answer "no" rather
        # than advertising an action nobody was authorized for.
        return bool(user and can_submit_task(user, obj))

    def get_can_annotate(self, obj) -> bool:
        from .services import can_annotate_task

        user = self._user()
        return bool(user and can_annotate_task(user, obj))

    def get_review_history(self, obj) -> list[dict]:
        return [
            {
                "id": row.id,
                "round_number": row.round_number,
                "annotator_username": row.annotator.get_username() if row.annotator else "",
                "submitted_at": row.submitted_at,
                "superseded_at": row.superseded_at,
                "superseded_reason": row.superseded_reason,
                "source": row.source,
                "review_status": row.review_status,
                "reviews": ReviewRecordSerializer(row.reviews.all(), many=True).data,
            }
            # `.all()` on purpose: any filter/select_related here would build a
            # fresh queryset and ignore the caller's prefetch cache, turning
            # every list view into one submissions query per task (plus one per
            # review row). List views prefetch this chain; see ProjectTasksView.
            for row in obj.submissions.all()
        ]

    class Meta:
        model = AnnotationTask
        fields = [
            "id",
            "project",
            "project_title",
            "dataset",
            "dataset_metadata",
            "voxel_size_z",
            "voxel_size_y",
            "voxel_size_x",
            "shape_z",
            "shape_y",
            "shape_x",
            "file_format",
            "volume_status",
            "ready_streaming",
            "streaming_status",
            "streaming_error",
            "region_ready_streaming",
            "region_streaming_status",
            "region_streaming_error",
            "has_region_mask",
            "volume",
            "volume_name",
            "image_location",
            "region_mask_location",
            "region_mask_coverage",
            "label_location",
            "label_type",
            "assigned_to",
            "assigned_to_username",
            "z_start",
            "z_end",
            "y_start",
            "y_end",
            "x_start",
            "x_end",
            "task_type",
            "review_history",
            "status",
            "priority",
            "difficulty",
            "instructions",
            "deadline",
            "frame_label",
            "annotation_locked",
            "can_submit",
            "can_annotate",
            "submission_count",
            "last_decision",
            "last_decision_at",
            "last_decision_by",
            "last_decision_by_username",
            "last_decision_comments",
            "last_decision_source",
            "created_at",
            "assigned_at",
            "submitted_at",
            "approved_at",
        ]
        read_only_fields = [
            "project",
            "volume",
            "annotation_locked",
            "submission_count",
            "last_decision",
            "last_decision_at",
            "last_decision_by",
            "last_decision_comments",
            "last_decision_source",
            "created_at",
            "assigned_at",
            "submitted_at",
            "approved_at",
            "review_history",
        ]


class ReviewRecordSerializer(serializers.ModelSerializer):
    reviewer_username = serializers.CharField(
        source="reviewer.username", read_only=True, default=""
    )

    class Meta:
        model = ReviewRecord
        fields = [
            "id",
            "submission",
            "reviewer",
            "reviewer_username",
            "decision",
            "source",
            "comments",
            "reviewed_at",
        ]


class AnnotationSubmissionSerializer(serializers.ModelSerializer):
    annotator_username = serializers.CharField(
        source="annotator.username", read_only=True, default=""
    )
    task_detail = AnnotationTaskSerializer(source="task", read_only=True)
    reviews = ReviewRecordSerializer(many=True, read_only=True)

    class Meta:
        model = AnnotationSubmission
        fields = [
            "id",
            "task",
            "task_detail",
            "annotator",
            "annotator_username",
            "label_file",
            "source",
            "review_status",
            "superseded_reason",
            "notes",
            "qc_status",
            "qc_report",
            "reviews",
            "round_number",
            "supersedes",
            "superseded_at",
            "is_current",
            "submitted_at",
        ]


class SubmitTaskSerializer(serializers.Serializer):
    label_file = serializers.FileField()
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class SubmitInappTaskSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class ReviewSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(
        choices=["approved", "rejected", "revision_requested"]
    )
    comments = serializers.CharField(required=False, allow_blank=True, default="")
    # Approve-only: the manager's "the annotator may keep working on this"
    # switch. Defaults to False, i.e. approve means done — reopening is a
    # deliberate act, not something you get by forgetting a checkbox.
    allow_further_annotation = serializers.BooleanField(required=False, default=False)


class AssignmentPlanTaskSerializer(serializers.ModelSerializer):
    """Bounded task shape for the push editor.

    Deliberately excludes labels IO, review history, permission gates and the
    review history. A project with hundreds of tasks must not turn one plan load
    into hundreds of history queries or a proxy-sized JSON response.
    """

    volume_name = serializers.CharField(source="volume.name", read_only=True)
    file_format = serializers.CharField(source="volume.file_format", read_only=True)
    shape_z = serializers.IntegerField(source="volume.shape_z", read_only=True)
    shape_y = serializers.IntegerField(source="volume.shape_y", read_only=True)
    shape_x = serializers.IntegerField(source="volume.shape_x", read_only=True)
    voxel_size_z = serializers.FloatField(source="volume.voxel_size_z", read_only=True)
    voxel_size_y = serializers.FloatField(source="volume.voxel_size_y", read_only=True)
    voxel_size_x = serializers.FloatField(source="volume.voxel_size_x", read_only=True)
    has_region_mask = serializers.BooleanField(
        source="volume.has_region_mask", read_only=True
    )
    region_mask_coverage = serializers.FloatField(
        source="volume.region_mask_coverage", read_only=True
    )
    label_type = serializers.CharField(source="volume.label_type", read_only=True)
    assigned_to_username = serializers.CharField(
        source="assigned_to.username", read_only=True, default=""
    )

    class Meta:
        model = AnnotationTask
        fields = [
            "id", "project", "volume", "volume_name", "file_format",
            "shape_z", "shape_y", "shape_x",
            "voxel_size_z", "voxel_size_y", "voxel_size_x",
            "has_region_mask", "region_mask_coverage", "label_type",
            "assigned_to", "assigned_to_username", "z_start", "z_end",
            "task_type", "status", "priority", "difficulty", "instructions",
            "deadline", "annotation_locked",
        ]


class HardCaseSerializer(serializers.ModelSerializer):
    """One row of the Hard Cases inbox / project list.

    Carries the viewer's own permissions (``can_annotate`` / ``can_take_down``)
    so the list and the case page never re-derive the matrix client-side; both
    come from ``annotation.services``. Needs ``context["request"]``.
    """

    project_title = serializers.CharField(source="project.title", read_only=True, default="")
    volume_name = serializers.CharField(source="volume.name", read_only=True, default="")
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True, default=""
    )
    resolved_by_username = serializers.CharField(
        source="resolved_by.username", read_only=True, default=""
    )
    task_status = serializers.CharField(source="task.status", read_only=True, default="")
    z_start = serializers.IntegerField(source="task.z_start", read_only=True)
    z_end = serializers.IntegerField(source="task.z_end", read_only=True)
    url = serializers.CharField(source="path", read_only=True)
    app_url = serializers.CharField(source="app_path", read_only=True)
    can_annotate = serializers.SerializerMethodField()
    can_take_down = serializers.SerializerMethodField()

    class Meta:
        model = HardCase
        fields = [
            "id",
            "token",
            "task",
            "task_status",
            "project",
            "project_title",
            "volume",
            "volume_name",
            "label_id",
            "note",
            "z_start",
            "z_end",
            "status",
            "revoked",
            "created_by",
            "created_by_username",
            "created_at",
            "resolved_by",
            "resolved_by_username",
            "resolved_at",
            "url",
            "app_url",
            "can_annotate",
            "can_take_down",
        ]

    def _user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def get_can_annotate(self, obj) -> bool:
        from .services import can_annotate_hard_case

        user = self._user()
        return bool(user and can_annotate_hard_case(user, obj))

    def get_can_take_down(self, obj) -> bool:
        from .services import can_take_down_hard_case

        user = self._user()
        return bool(user and can_take_down_hard_case(user, obj))


class PlanEntrySerializer(serializers.Serializer):
    """One row of a manager-edited assignment plan.

    Only ``task_id`` is required. ``annotator_id`` is applied only when the key
    is present (``None`` unassigns), and each task field is updated only when
    supplied — so the client can send just what the manager changed.
    """

    task_id = serializers.IntegerField()
    annotator_id = serializers.IntegerField(required=False, allow_null=True)
    priority = serializers.IntegerField(required=False)
    difficulty = serializers.IntegerField(required=False)
    instructions = serializers.CharField(required=False, allow_blank=True)
    deadline = serializers.DateField(required=False, allow_null=True)


class AssignmentPlanSerializer(serializers.Serializer):
    entries = PlanEntrySerializer(many=True)
