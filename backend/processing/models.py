"""ProcessingJob: a queued unit of heavy/asynchronous computation.

Jobs represent work that must not run inside an HTTP request — ingestion, model
inference, task generation, visualization conversion, mesh generation,
publishing, etc. They are created by the service layer, claimed by the
dispatcher (``manage.py run_processing_dispatcher``), and executed through a
pluggable processing backend (local/mock or SLURM).

A job may relate to a Project, a Volume, and/or an AnnotationTask; all links are
optional and ``SET_NULL`` so deleting a domain object never destroys job
history.
"""

from django.conf import settings
from django.db import models

from core.choices import (
    ProcessingBackend,
    ProcessingJobStatus,
    ProcessingJobType,
)


class ProcessingJob(models.Model):
    job_type = models.CharField(max_length=30, choices=ProcessingJobType.choices)
    backend = models.CharField(
        max_length=20,
        choices=ProcessingBackend.choices,
        default=ProcessingBackend.LOCAL,
    )
    status = models.CharField(
        max_length=20,
        choices=ProcessingJobStatus.choices,
        default=ProcessingJobStatus.QUEUED,
    )

    # Optional links to the domain objects the job acts on.
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processing_jobs",
    )
    volume = models.ForeignKey(
        "volumes.Volume",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processing_jobs",
    )
    task = models.ForeignKey(
        "annotation.AnnotationTask",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processing_jobs",
    )

    # Backend bookkeeping.
    external_job_id = models.CharField(max_length=255, blank=True)
    config = models.JSONField(default=dict, blank=True)
    input_paths = models.JSONField(default=dict, blank=True)
    output_paths = models.JSONField(default=dict, blank=True)
    log_path = models.CharField(max_length=1024, blank=True)
    error_message = models.TextField(blank=True)
    retry_count = models.PositiveIntegerField(default=0)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processing_jobs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self) -> str:
        return f"ProcessingJob #{self.pk} {self.job_type} ({self.status})"

    @property
    def is_active(self) -> bool:
        from core.choices import ACTIVE_JOB_STATUSES

        return self.status in ACTIVE_JOB_STATUSES

    @property
    def is_terminal(self) -> bool:
        from core.choices import TERMINAL_JOB_STATUSES

        return self.status in TERMINAL_JOB_STATUSES
