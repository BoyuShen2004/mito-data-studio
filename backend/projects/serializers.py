from rest_framework import serializers

from core.lifecycle import classify_project

from .models import Dataset, Project, ProjectMembership


class ProjectMembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    display_name = serializers.CharField(
        source="user.profile.display_name", read_only=True, default=""
    )
    added_by_username = serializers.CharField(
        source="added_by.username", read_only=True, default=""
    )

    class Meta:
        model = ProjectMembership
        fields = [
            "id", "user_id", "username", "display_name",
            "added_by_username", "created_at",
        ]


class DatasetSerializer(serializers.ModelSerializer):
    volume_count = serializers.IntegerField(source="volumes.count", read_only=True)
    project_title = serializers.CharField(source="project.title", read_only=True)

    class Meta:
        model = Dataset
        fields = [
            "id",
            "project",
            "project_title",
            "name",
            "description",
            "image_directory",
            "region_mask_directory",
            "mask_directory",
            "metadata",
            "volume_count",
            "created_at",
        ]
        read_only_fields = ["created_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        from accounts.roles import can_register_data
        request = self.context.get("request")
        if request is not None and not can_register_data(request.user):
            for key in ("image_directory", "region_mask_directory", "mask_directory"):
                data.pop(key, None)
        return data


class ProjectSerializer(serializers.ModelSerializer):
    institution_name = serializers.CharField(
        source="institution.name", read_only=True, default=""
    )
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True, default=""
    )
    reviewed_by_username = serializers.CharField(
        source="reviewed_by.username", read_only=True, default=""
    )
    volume_count = serializers.IntegerField(source="volumes.count", read_only=True)
    task_count = serializers.IntegerField(source="tasks.count", read_only=True)
    datasets = DatasetSerializer(many=True, read_only=True)
    dataset_count = serializers.IntegerField(source="datasets.count", read_only=True)
    # The New / To Proofread / Done bucket, computed from the review gate and
    # task rollup (see core.lifecycle).
    lifecycle = serializers.SerializerMethodField()

    def get_lifecycle(self, obj) -> str:
        return classify_project(obj)

    class Meta:
        model = Project
        fields = [
            "id",
            "title",
            "dataset",
            "datasets",
            "dataset_count",
            "institution",
            "institution_name",
            "description",
            "metadata",
            "annotation_target",
            "annotation_type",
            "workflow_type",
            "lifecycle",
            "status",
            "priority",
            "paused",
            "teams",
            "working_team",
            "deadline",
            "created_by",
            "created_by_username",
            "manager_reviewed",
            "reviewed_by",
            "reviewed_by_username",
            "reviewed_at",
            "volume_count",
            "task_count",
            "created_at",
        ]
        read_only_fields = [
            "created_by",
            "created_at",
            "manager_reviewed",
            "reviewed_by",
            "reviewed_at",
            "working_team",
        ]
