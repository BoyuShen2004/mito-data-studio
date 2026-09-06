from rest_framework import serializers

from core.choices import (
    ProcessingJobStatus,
    ProcessingJobType,
    WRITABLE_LABEL_TYPES,
    LabelType,
)

from .models import Volume


class VolumeSerializer(serializers.ModelSerializer):
    has_label = serializers.BooleanField(read_only=True)
    image_location = serializers.CharField(read_only=True)
    region_mask_location = serializers.CharField(read_only=True)
    has_region_mask = serializers.BooleanField(read_only=True)
    region_mask_empty = serializers.BooleanField(read_only=True, allow_null=True)
    label_location = serializers.CharField(read_only=True)
    dataset_name = serializers.CharField(source="dataset.name", read_only=True, default="")
    streaming_status = serializers.SerializerMethodField()
    streaming_error = serializers.SerializerMethodField()
    pyramid_job_id = serializers.SerializerMethodField()
    region_streaming_status = serializers.SerializerMethodField()
    region_streaming_error = serializers.SerializerMethodField()
    region_pyramid_job_id = serializers.SerializerMethodField()

    @staticmethod
    def _pyramid_job(volume, layer="image"):
        """Latest build job for one layer, from the prefetch when there is one.

        Both layers are BUILD_PYRAMID rows, so they arrive in the *same*
        prefetch and are split here — asking the database twice per volume for
        two statuses is exactly the N+1 the prefetch exists to avoid.
        """
        from volumes.pyramid.jobs import job_layer

        cache = getattr(volume, "_latest_pyramid_jobs", None)
        if cache is None:
            cache = volume._latest_pyramid_jobs = {}
        if layer in cache:
            return cache[layer]
        rows = getattr(volume, "pyramid_jobs", None)
        if rows is None:
            # No prefetch (single-volume detail): read the rows once and keep
            # them, so asking for the second layer is not a second query.
            rows = getattr(volume, "_pyramid_job_rows", None)
            if rows is None:
                rows = volume._pyramid_job_rows = list(
                    volume.processing_jobs.filter(
                        job_type=ProcessingJobType.BUILD_PYRAMID
                    ).order_by("-created_at")
                )
        jobs = [
            job
            for job in rows
            if job.job_type == ProcessingJobType.BUILD_PYRAMID
            and job_layer(job) == layer
        ]
        job = max(jobs, key=lambda item: (item.created_at, item.pk)) if jobs else None
        cache[layer] = job
        return job

    @staticmethod
    def _status_for(ready, job):
        if ready:
            return "ready"
        if job is None:
            return "not_built"
        if job.status in {
            ProcessingJobStatus.QUEUED,
            ProcessingJobStatus.SUBMITTED,
            ProcessingJobStatus.RUNNING,
        }:
            return "building"
        if job.status in {
            ProcessingJobStatus.FAILED,
            ProcessingJobStatus.CANCELLED,
            ProcessingJobStatus.SUCCEEDED,
        }:
            return "failed"
        return "not_built"

    @staticmethod
    def _error_for(job):
        if job is None or job.status != ProcessingJobStatus.FAILED:
            return ""
        return (job.error_message or "Pyramid build failed.")[:500]

    def get_streaming_status(self, volume):
        return self._status_for(volume.ready_streaming, self._pyramid_job(volume))

    def get_streaming_error(self, volume):
        return self._error_for(self._pyramid_job(volume))

    def get_pyramid_job_id(self, volume):
        job = self._pyramid_job(volume)
        return job.pk if job is not None else None

    def get_region_streaming_status(self, volume):
        # "absent" is not "not built": a volume with no ROI has nothing to
        # stream, and showing it as unbuilt would invite a pointless rebuild.
        if not volume.has_region_mask:
            return "absent"
        return self._status_for(
            volume.region_ready_streaming, self._pyramid_job(volume, "region")
        )

    def get_region_streaming_error(self, volume):
        if not volume.has_region_mask:
            return ""
        return self._error_for(self._pyramid_job(volume, "region"))

    def get_region_pyramid_job_id(self, volume):
        job = self._pyramid_job(volume, "region")
        return job.pk if job is not None else None

    class Meta:
        model = Volume
        fields = [
            "id",
            "project",
            "dataset",
            "dataset_name",
            "name",
            "ready_streaming",
            "streaming_status",
            "streaming_error",
            "pyramid_job_id",
            "region_ready_streaming",
            "region_streaming_status",
            "region_streaming_error",
            "region_pyramid_job_id",
            "image_path",
            "image_file",
            "region_mask_path",
            "region_mask_file",
            "label_path",
            "label_file",
            "label_type",
            "shape_z",
            "shape_y",
            "shape_x",
            "voxel_size_z",
            "voxel_size_y",
            "voxel_size_x",
            "file_format",
            "metadata",
            "status",
            "has_label",
            "image_location",
            "region_mask_location",
            "has_region_mask",
            "region_mask_coverage",
            "region_mask_empty",
            "label_location",
            "created_at",
        ]
        read_only_fields = [
            "project",
            "status",
            "created_at",
            "ready_streaming",
            "streaming_status",
            "streaming_error",
            "pyramid_job_id",
            "region_ready_streaming",
            "region_streaming_status",
            "region_streaming_error",
            "region_pyramid_job_id",
            "region_mask_coverage",
            "region_mask_empty",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        from accounts.roles import can_register_data
        request = self.context.get("request")
        if request is not None and not can_register_data(request.user):
            for key in (
                "image_path", "image_file", "region_mask_path", "region_mask_file",
                "label_path", "label_file", "image_location", "region_mask_location",
                "label_location",
            ):
                data.pop(key, None)
        return data


class HpcScanSerializer(serializers.Serializer):
    """Scan an image directory plus an optional, separate mask directory."""

    image_directory = serializers.CharField(required=False, allow_blank=True, default="")
    region_mask_directory = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    mask_directory = serializers.CharField(required=False, allow_blank=True, default="")
    # Older clients sent a single directory; treat it as the image directory.
    hpc_directory = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if not (attrs.get("image_directory") or attrs.get("hpc_directory") or "").strip():
            raise serializers.ValidationError(
                {"image_directory": "An image directory is required."}
            )
        return attrs


class RegisterDataFileSerializer(serializers.Serializer):
    path = serializers.CharField(required=False, allow_blank=True, default="")
    name = serializers.CharField(required=False, allow_blank=True, default="")
    # Deprecated alias for ``name`` (old crop-id clients).
    chunk_id = serializers.CharField(required=False, allow_blank=True, default="")


class RegisterDataPairSerializer(serializers.Serializer):
    image = serializers.CharField()
    region_mask = serializers.CharField(required=False, allow_blank=True, default="")
    mask = serializers.CharField(required=False, allow_blank=True, default="")
    name = serializers.CharField(required=False, allow_blank=True, default="")
    # Deprecated alias for ``name``.
    chunk_id = serializers.CharField(required=False, allow_blank=True, default="")


# Optional, non-image-derived biomedical metadata (see Mitoverse). Resolution,
# shape, and mitochondria counts are derived from the files, never entered here.
METADATA_FIELDS = [
    "organism",
    "tissue",
    "cell_type",
    "imaging_modality",
    "imaging_instrument",
    "experimental_condition",
    "sample_condition",
    "dataset_source",
    "publication",
    "description",
    "notes",
]


class RegisterDataSerializer(serializers.Serializer):
    """Shared payload used by requesters and managers to register data."""

    dataset = serializers.CharField()
    # Deprecated: previously named a shared parent for crop grouping. Ignored —
    # each registered file is its own volume named by ``name``/case id.
    volume = serializers.CharField(required=False, allow_blank=True, default="")
    # Masks commonly live in their own directory (nnU-Net imagesTr/labelsTr);
    # mask_directory is optional and defaults to the image directory.
    image_directory = serializers.CharField(required=False, allow_blank=True, default="")
    region_mask_directory = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    mask_directory = serializers.CharField(required=False, allow_blank=True, default="")
    hpc_directory = serializers.CharField(required=False, allow_blank=True, default="")
    # Data is registered *into* a project, which is created first: a project
    # describes the work, and holds the datasets registered against it.
    project = serializers.IntegerField()
    annotation_type = serializers.CharField(required=False, allow_blank=True)
    # Image+mask pairs (preferred) and/or image-only files. When both are
    # omitted the directory is auto-scanned and all detected pairs registered.
    pairs = RegisterDataPairSerializer(many=True, required=False)
    files = RegisterDataFileSerializer(many=True, required=False)
    # Writable types only. `proofread` stays in LabelType for legacy rows but
    # is retired for new writes, so advertising it here would document a value
    # the service always rejects.
    label_type = serializers.ChoiceField(
        choices=WRITABLE_LABEL_TYPES, required=False, allow_blank=True
    )
    metadata = serializers.DictField(required=False)

    def validate_dataset(self, value):
        if not value.strip():
            raise serializers.ValidationError("A dataset name is required.")
        return value

    def validate(self, attrs):
        if not (attrs.get("image_directory") or attrs.get("hpc_directory") or "").strip():
            raise serializers.ValidationError(
                {"image_directory": "An image directory is required."}
            )
        return attrs
