from django.db import models
from django.db.models import Q

from core.choices import FileFormat, LabelType, TimeTracking, VolumeStatus
from core.storage import get_mito_storage


def volume_image_upload_to(instance, filename):
    return f"volumes/{instance.project_id}/images/{filename}"


def volume_label_upload_to(instance, filename):
    return f"volumes/{instance.project_id}/labels/{filename}"


def volume_region_mask_upload_to(instance, filename):
    return f"volumes/{instance.project_id}/region-masks/{filename}"


class Volume(models.Model):
    """An image volume registered under a project.

    The image may be *registered* (a path relative to ``MITO_DATA_ROOT`` in
    ``image_path``) or *uploaded* (``image_file``). Labels are optional and
    their kind is recorded in ``label_type``, which drives task creation.
    """

    # The owning project. Denormalised from ``dataset.project`` because tasks,
    # assignment, and progress all query volumes by project; keep the two in
    # step via ``volumes.services.register_volume``/``set_volume_dataset``.
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="volumes"
    )
    # The dataset this volume pair belongs to. Nullable only for rows created
    # before datasets existed.
    dataset = models.ForeignKey(
        "projects.Dataset",
        on_delete=models.CASCADE,
        related_name="volumes",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)

    # --- Phase 11: interactive derivative (ADR-009) -------------------------
    # Additive only: the source TIFF above remains the source of truth, and a
    # volume with no pyramid is valid — an absent derivative is an accurate
    # absence, not a missing record needing backfill.
    ready_streaming = models.BooleanField(
        default=False,
        help_text=(
            "A validated Zarr v3 pyramid exists for this volume. Flipped only "
            "after random-chunk checksums pass (doc 20 §Pyramid job)."
        ),
    )
    # Paths, mags, shapes and checksums — never voxels (ADR-005 conflict B).
    pyramid_metadata = models.JSONField(default=dict, blank=True)

    # The same derivative contract for the read-only region mask, tracked
    # separately because the two layers are built, promoted and rolled back
    # independently: a volume may stream its image and still read its ROI from
    # the source file, or the reverse (ADR-009 addendum §Region layer).
    region_ready_streaming = models.BooleanField(
        default=False,
        help_text=(
            "A validated Zarr v3 pyramid exists for this volume's region mask. "
            "Flipped only after random-chunk checksums pass."
        ),
    )
    region_pyramid_metadata = models.JSONField(default=dict, blank=True)

    # Registered (path relative to MITO_DATA_ROOT) or uploaded image.
    image_path = models.CharField(max_length=1024, blank=True)
    image_file = models.FileField(
        storage=get_mito_storage,
        upload_to=volume_image_upload_to,
        blank=True,
        null=True,
    )

    # Immutable reference layer: visible in the editor but never a write target.
    region_mask_path = models.CharField(max_length=1024, blank=True)
    region_mask_file = models.FileField(
        storage=get_mito_storage,
        upload_to=volume_region_mask_upload_to,
        blank=True,
        null=True,
    )
    # Cached fraction of region-mask voxels whose value is non-zero. ``None``
    # means no mask (or a mask not yet successfully inspected); importantly,
    # a real empty mask is stored as ``0.0`` and is not confused with absence.
    region_mask_coverage = models.FloatField(null=True, blank=True)

    # Optional label. May be absent, a prediction, proofread, or partial.
    label_path = models.CharField(max_length=1024, blank=True)
    label_file = models.FileField(
        storage=get_mito_storage,
        upload_to=volume_label_upload_to,
        blank=True,
        null=True,
    )
    label_type = models.CharField(
        max_length=20, choices=LabelType.choices, default=LabelType.NONE
    )

    shape_z = models.PositiveIntegerField(null=True, blank=True)
    shape_y = models.PositiveIntegerField(null=True, blank=True)
    shape_x = models.PositiveIntegerField(null=True, blank=True)

    voxel_size_z = models.FloatField(null=True, blank=True)
    voxel_size_y = models.FloatField(null=True, blank=True)
    voxel_size_x = models.FloatField(null=True, blank=True)

    file_format = models.CharField(
        max_length=10, choices=FileFormat.choices, default=FileFormat.TIFF
    )
    metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20, choices=VolumeStatus.choices, default=VolumeStatus.REGISTERED
    )

    # --- Annotation time tracking eligibility (durable, decided once) -------
    # New volumes are eligible. The rollout migration marks volumes that were
    # *already assigned* when the feature shipped as ``LEGACY_EXEMPT``, because
    # their annotation started unmeasured and any total we could show would be
    # a fraction of the real effort presented as the whole of it.
    #
    # This is stored rather than derived on purpose: deriving it from "is this
    # volume assigned right now?" would silently promote an exempt volume to
    # eligible the moment it was reassigned, and start reporting a partial
    # number as if it were complete. Changing it afterwards is an explicit
    # administrative act, not a side effect of assignment.
    time_tracking = models.CharField(
        max_length=20,
        choices=TimeTracking.choices,
        default=TimeTracking.ELIGIBLE,
        db_index=True,
        help_text=(
            "Whether annotation time is measured for this volume. "
            "Legacy-exempt volumes always report '-', never zero."
        ),
    )
    # When the classification above was decided, and by what. Rollout metadata:
    # it distinguishes "the migration classified this" from "created eligible
    # afterwards", which is the difference between an unknown total and a real
    # zero. Null for volumes created after rollout through the normal path.
    time_tracking_set_at = models.DateTimeField(null=True, blank=True)
    time_tracking_reason = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "image_path"],
                condition=Q(dataset__isnull=False) & ~Q(image_path=""),
                name="unique_registered_image_per_dataset",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.project.title})"

    @property
    def has_label(self) -> bool:
        return self.label_type != LabelType.NONE and bool(
            self.label_path or self.label_file
        )

    @property
    def image_location(self) -> str:
        """Path or storage name identifying the image, whichever is set."""
        if self.image_file:
            return self.image_file.name
        return self.image_path

    @property
    def label_location(self) -> str:
        if self.label_file:
            return self.label_file.name
        return self.label_path

    @property
    def region_mask_location(self) -> str:
        if self.region_mask_file:
            return self.region_mask_file.name
        return self.region_mask_path

    @property
    def has_region_mask(self) -> bool:
        return bool(self.region_mask_location)

    @property
    def region_mask_empty(self):
        if not self.has_region_mask or self.region_mask_coverage is None:
            return None
        return self.region_mask_coverage == 0
