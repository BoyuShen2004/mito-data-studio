from rest_framework import serializers

from .models import ProcessingJob


class ProcessingJobSerializer(serializers.ModelSerializer):
    project_title = serializers.CharField(source="project.title", read_only=True, default="")
    volume_name = serializers.CharField(source="volume.name", read_only=True, default="")

    class Meta:
        model = ProcessingJob
        fields = [
            "id",
            "job_type",
            "backend",
            "status",
            "project",
            "project_title",
            "volume",
            "volume_name",
            "task",
            "external_job_id",
            "config",
            "input_paths",
            "output_paths",
            "log_path",
            "error_message",
            "retry_count",
            "created_by",
            "created_at",
            "submitted_at",
            "started_at",
            "finished_at",
        ]
        read_only_fields = fields
