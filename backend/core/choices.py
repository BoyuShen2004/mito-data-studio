"""Shared enumerated choices used across apps.

Centralising these keeps the label-state -> task-type mapping and the task
lifecycle consistent between models, the service layer, and the service-layer
callers (DRF views, admin actions, management commands).
"""

from django.db import models


class TeamRole(models.TextChoices):
    """A user's standing *within one team* (Phase 1).

    Orthogonal to :class:`UserRole`, which is org-wide. A person may be a
    plain annotator org-wide yet manage one team's queue, which is exactly the
    distinction WEBKNOSSOS draws between "user" and "team manager".
    """

    MEMBER = "member", "Member"
    MANAGER = "manager", "Team manager"


class AuditVerb(models.TextChoices):
    """What happened, for the append-only audit log."""

    TEAM_MEMBER_ADDED = "team.member_added", "Team member added"
    TEAM_MEMBER_REMOVED = "team.member_removed", "Team member removed"
    TEAM_ROLE_CHANGED = "team.role_changed", "Team role changed"
    PROJECT_TEAM_GRANTED = "project.team_granted", "Project access granted to team"
    PROJECT_TEAM_REVOKED = "project.team_revoked", "Project access revoked from team"
    # Retained as historical audit vocabulary after experience/claim removal.
    EXPERIENCE_SET = "experience.set", "Experience set"
    EXPERIENCE_CLEARED = "experience.cleared", "Experience cleared"
    PERMISSION_DENIED = "permission.denied", "Permission denied"
    TASK_CLAIMED = "task.claimed", "Task instance claimed"
    TASK_ASSIGNED = "task.assigned", "Task instance assigned manually"
    TASK_TRANSFERRED = "task.transferred", "Task instance transferred"
    TASK_RELEASED = "task.released", "Task instance released"
    TASK_LEASE_EXPIRED = "task.lease_expired", "Task instance lease expired"
    # Phase 5 — review loop.
    SUBMISSION_CREATED = "submission.created", "Submission recorded"
    SUBMISSION_SUPERSEDED = "submission.superseded", "Submission superseded"
    REVIEW_RECORDED = "review.recorded", "Review decision recorded"


class UserRole(models.TextChoices):
    MANAGER = "manager", "Manager"
    ANNOTATOR = "annotator", "Annotator"
    REQUESTER = "requester", "Requester"
    # Legacy roles kept for backwards compatibility with existing records.
    CLIENT = "client", "Client"
    REVIEWER = "reviewer", "Reviewer"


class AnnotationType(models.TextChoices):
    SEMANTIC = "semantic_segmentation", "Semantic segmentation"
    INSTANCE = "instance_segmentation", "Instance segmentation"
    PROOFREADING = "proofreading", "Proofreading"


class WorkflowType(models.TextChoices):
    """The high-level pipeline requested for a dataset.

    * ``annotation``   — labels are created from raw/unlabeled data.
    * ``proofreading`` — existing predictions/labels are corrected.
    * ``segmentation`` — a processing/model-inference job generates a result,
      which may be delivered directly or optionally continue into proofreading.

    These share one dataset registration, volume metadata, task assignment,
    submission, review, and result-tracking implementation; they differ only in
    configuration and service-layer branching, not in duplicated pipelines.
    """

    ANNOTATION = "annotation", "Annotation"
    PROOFREADING = "proofreading", "Proofreading"
    SEGMENTATION = "segmentation", "Segmentation"


# Maps the (older, more specific) annotation_type to a workflow_type. Used to
# backfill workflow_type and to default it when only annotation_type is given.
ANNOTATION_TYPE_TO_WORKFLOW = {
    AnnotationType.SEMANTIC: WorkflowType.ANNOTATION,
    AnnotationType.INSTANCE: WorkflowType.ANNOTATION,
    AnnotationType.PROOFREADING: WorkflowType.PROOFREADING,
}


class ProjectStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    IN_ANNOTATION = "in_annotation", "In annotation"
    IN_REVIEW = "in_review", "In review"
    COMPLETED = "completed", "Completed"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"


class LabelType(models.TextChoices):
    NONE = "none", "No label"
    PREDICTION = "prediction", "Model prediction"
    PROOFREAD = "proofread", "Proofread"
    PARTIAL = "partial", "Partial"


# `proofread` is retired for new writes but still present on legacy rows, so it
# stays in LabelType (reads, admin, task-type mapping) while being excluded
# from every write surface. Keep API choices and service validation reading
# from this one list.
WRITABLE_LABEL_TYPES = [
    LabelType.NONE.value,
    LabelType.PARTIAL.value,
    LabelType.PREDICTION.value,
]

# The subset a volume that actually has a mask may carry.
MASKED_LABEL_TYPES = [LabelType.PARTIAL.value, LabelType.PREDICTION.value]


class FileFormat(models.TextChoices):
    TIFF = "tiff", "TIFF"
    ZARR = "zarr", "Zarr"
    HDF5 = "hdf5", "HDF5"
    NIFTI = "nifti", "NIfTI"
    N5 = "n5", "N5"
    OTHER = "other", "Other"


class VolumeStatus(models.TextChoices):
    # A volume is one assignable unit; there is no "split into tasks" state.
    REGISTERED = "registered", "Registered"
    IN_ANNOTATION = "in_annotation", "In annotation"
    COMPLETED = "completed", "Completed"


class TaskType(models.TextChoices):
    MANUAL_ANNOTATION = "manual_annotation", "Manual annotation"
    PREDICTION_PROOFREADING = "prediction_proofreading", "Prediction proofreading"
    FINAL_REVIEW = "final_review", "Final review"
    QC_REVIEW = "qc_review", "QC review"


class TaskStatus(models.TextChoices):
    UNASSIGNED = "unassigned", "Unassigned"
    ASSIGNED = "assigned", "Assigned"
    IN_PROGRESS = "in_progress", "In progress"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    REVISION_REQUESTED = "revision_requested", "Revision requested"


# Statuses that count against an annotator's active-task capacity.
ACTIVE_TASK_STATUSES = (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS)


class PriorityLevel(models.IntegerChoices):
    """How urgently a task should be picked up (higher is assigned first)."""

    LOWEST = 1, "Lowest"
    LOW = 2, "Low"
    NORMAL = 3, "Normal"
    HIGH = 4, "High"
    URGENT = 5, "Urgent"


class DifficultyLevel(models.IntegerChoices):
    """How hard a task is to annotate (higher is harder)."""

    VERY_EASY = 1, "Very easy"
    EASY = 2, "Easy"
    MODERATE = 3, "Moderate"
    HARD = 4, "Hard"
    VERY_HARD = 5, "Very hard"


class QCStatus(models.TextChoices):
    NOT_RUN = "not_run", "Not run"
    PASSED = "passed", "Passed"
    WARNING = "warning", "Warning"
    FAILED = "failed", "Failed"


class ReviewDecision(models.TextChoices):
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    REVISION_REQUESTED = "revision_requested", "Revision requested"


class HardCaseStatus(models.TextChoices):
    """Lifecycle of a project hard case (``annotation.models.HardCase``).

    ``open`` cases sit at the top of the project/inbox lists; "take down"
    moves one to ``resolved`` (still listed, still viewable — just no longer
    an open question). Distinct from ``revoked``, which kills the *public*
    token without changing the case's standing inside the project.
    """

    OPEN = "open", "Open"
    RESOLVED = "resolved", "Resolved"


class SubmissionSource(models.TextChoices):
    """Where an :class:`~annotation.models.AnnotationSubmission`'s content
    came from — drives both QC (``annotation.quality_control.adapters.
    basic``) and what "approve" actually does (``annotation.services.
    approve_submission``)."""

    UPLOAD = "upload", "Uploaded file"
    INAPP = "inapp", "In-app editor"


class SubmissionReviewStatus(models.TextChoices):
    """Durable review state for one submission channel."""

    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    REVISION_REQUESTED = "revision_requested", "Revision requested"
    VOIDED = "voided", "Voided"


# --- Processing / HPC jobs --------------------------------------------------

class ProcessingBackend(models.TextChoices):
    LOCAL = "local", "Local / mock"
    SLURM = "slurm", "SLURM"


class ProcessingJobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SUBMITTED = "submitted", "Submitted"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


# Job statuses that are still active (a dispatcher should keep polling them).
ACTIVE_JOB_STATUSES = (
    ProcessingJobStatus.SUBMITTED,
    ProcessingJobStatus.RUNNING,
)
TERMINAL_JOB_STATUSES = (
    ProcessingJobStatus.SUCCEEDED,
    ProcessingJobStatus.FAILED,
    ProcessingJobStatus.CANCELLED,
)


class ProcessingJobType(models.TextChoices):
    INSPECT = "inspect", "Inspect"
    INGEST = "ingest", "Ingest"
    PREDICT = "predict", "Predict"
    SEED = "seed", "Seed"
    GENERATE_TASKS = "generate_tasks", "Generate tasks"
    QUALITY_CONTROL = "quality_control", "Quality control"
    CONVERT_VISUALIZATION = "convert_visualization", "Convert for visualization"
    GENERATE_MESH = "generate_mesh", "Generate mesh"
    BUILD_PYRAMID = "build_pyramid", "Build pyramid"
    PUBLISH = "publish", "Publish"


# Maps a volume's label state to the task type produced when splitting it.
# ``partial`` defaults to manual annotation but the manager may override.
LABEL_TYPE_TO_TASK_TYPE = {
    LabelType.NONE: TaskType.MANUAL_ANNOTATION,
    LabelType.PREDICTION: TaskType.PREDICTION_PROOFREADING,
    LabelType.PROOFREAD: TaskType.FINAL_REVIEW,
    LabelType.PARTIAL: TaskType.MANUAL_ANNOTATION,
}
