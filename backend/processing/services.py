"""Service layer for ProcessingJob creation and dispatch.

All job state transitions go through here so the DRF views, admin actions, and
the dispatcher management command share one implementation. Heavy work is never
run inside an HTTP request: the API/admin only *create* or *retry/cancel* jobs;
the dispatcher (``manage.py run_processing_dispatcher``) executes them.
"""

from __future__ import annotations

from django.db import connection, transaction
from django.utils import timezone

from core.choices import (
    ProcessingJobStatus,
    ProcessingJobType,
)

from .models import ProcessingJob
from .registry import get_processing_backend


def create_processing_job(
    *,
    job_type: str,
    created_by=None,
    project=None,
    volume=None,
    task=None,
    backend: str | None = None,
    config: dict | None = None,
    input_paths: dict | None = None,
) -> ProcessingJob:
    """Create a queued :class:`ProcessingJob`.

    ``backend`` defaults to the configured processing backend. The job is left
    ``queued``; the dispatcher submits it.
    """
    from django.conf import settings

    if job_type not in ProcessingJobType.values:
        raise ValueError(f"Unknown job_type: {job_type}")

    return ProcessingJob.objects.create(
        job_type=job_type,
        backend=backend or getattr(settings, "MITO_PROCESSING_BACKEND", "local"),
        status=ProcessingJobStatus.QUEUED,
        created_by=created_by,
        project=project,
        volume=volume,
        task=task,
        config=config or {},
        input_paths=input_paths or {},
    )


def claim_next_queued_job(*, job_types: tuple[str, ...] | None = None) -> ProcessingJob | None:
    """Atomically claim the oldest queued job, marking it submitted.

    On backends that support it (PostgreSQL) this uses
    ``select_for_update(skip_locked=True)`` so multiple dispatcher processes
    never claim the same job. On SQLite (dev/tests) row-locking is a no-op and a
    single dispatcher is assumed. Returns the claimed job or ``None``.
    """
    with transaction.atomic():
        qs = ProcessingJob.objects.filter(
            status=ProcessingJobStatus.QUEUED
        ).order_by("created_at")
        if job_types:
            qs = qs.filter(job_type__in=job_types)
        if connection.features.has_select_for_update:
            lock_kwargs = {}
            if connection.features.has_select_for_update_skip_locked:
                lock_kwargs["skip_locked"] = True
            qs = qs.select_for_update(**lock_kwargs)
        job = qs.first()
        if job is None:
            return None
        job.status = ProcessingJobStatus.SUBMITTED
        job.submitted_at = timezone.now()
        job.save(update_fields=["status", "submitted_at"])
        return job


def dispatch_job(job: ProcessingJob) -> ProcessingJob:
    """Submit a claimed job through its backend and record the result."""
    if (
        job.job_type == ProcessingJobType.BUILD_PYRAMID
        and job.backend == "local"
    ):
        # Pyramid builds are Python-native bounded jobs, not shell commands.
        # Execute them through their dedicated runner so the generic local
        # adapter cannot incorrectly "succeed" after writing only a mock marker.
        from volumes.pyramid.jobs import run_build

        try:
            run_build(job)
        except Exception:
            # run_build records the typed failure and timestamps before it
            # raises. A bad source must fail one job, not kill the dispatcher.
            job.refresh_from_db()
            return job
        job.refresh_from_db()
        return job
    backend = get_processing_backend(job.backend)
    result = backend.submit(job)
    _apply_result(job, result, started=True)
    _maybe_finish(job)
    return job


def poll_job(job: ProcessingJob) -> ProcessingJob:
    """Poll an active job and record any status change."""
    if not job.is_active:
        return job
    backend = get_processing_backend(job.backend)
    result = backend.poll(job)
    _apply_result(job, result)
    _maybe_finish(job)
    return job


def cancel_job(job: ProcessingJob) -> ProcessingJob:
    """Cancel a job (best effort via its backend)."""
    backend = get_processing_backend(job.backend)
    result = backend.cancel(job)
    _apply_result(job, result)
    _maybe_finish(job)
    return job


def retry_job(job: ProcessingJob) -> ProcessingJob:
    """Requeue a failed/cancelled job, incrementing its retry counter."""
    if not job.is_terminal:
        raise ValueError("Only terminal (failed/cancelled/succeeded) jobs can be retried.")
    job.status = ProcessingJobStatus.QUEUED
    job.retry_count += 1
    job.error_message = ""
    job.external_job_id = ""
    job.submitted_at = None
    job.started_at = None
    job.finished_at = None
    job.save(
        update_fields=[
            "status",
            "retry_count",
            "error_message",
            "external_job_id",
            "submitted_at",
            "started_at",
            "finished_at",
        ]
    )
    return job


def run_dispatch_once(
    *,
    max_new: int = 10,
    poll_active: bool = True,
    job_types: tuple[str, ...] | None = None,
) -> dict:
    """One dispatcher pass: submit queued jobs and poll active ones.

    Returns a summary dict. Intended to be called in a loop by the
    ``run_processing_dispatcher`` command (or once, in tests).
    """
    submitted = 0
    for _ in range(max_new):
        job = claim_next_queued_job(job_types=job_types)
        if job is None:
            break
        dispatch_job(job)
        submitted += 1

    polled = 0
    if poll_active:
        active = ProcessingJob.objects.filter(
            status__in=(
                ProcessingJobStatus.SUBMITTED,
                ProcessingJobStatus.RUNNING,
            )
        )
        if job_types:
            active = active.filter(job_type__in=job_types)
        for job in active:
            poll_job(job)
            polled += 1

    return {"submitted": submitted, "polled": polled}


# --- internals -------------------------------------------------------------

def _apply_result(job: ProcessingJob, result, *, started: bool = False) -> None:
    fields = ["status"]
    job.status = result.status
    if result.external_job_id:
        job.external_job_id = result.external_job_id
        fields.append("external_job_id")
    if result.output_paths:
        job.output_paths = {**(job.output_paths or {}), **result.output_paths}
        fields.append("output_paths")
    if result.log_path:
        job.log_path = result.log_path
        fields.append("log_path")
    if result.error_message:
        job.error_message = result.error_message
        fields.append("error_message")
    if started and job.started_at is None:
        job.started_at = timezone.now()
        fields.append("started_at")
    job.save(update_fields=fields)


def _maybe_finish(job: ProcessingJob) -> None:
    """Stamp finished_at and fire the lifecycle callback on terminal states."""
    if job.is_terminal and job.finished_at is None:
        job.finished_at = timezone.now()
        job.save(update_fields=["finished_at"])
    if job.is_terminal:
        on_job_finished(job)


def on_job_finished(job: ProcessingJob) -> None:
    """Service-layer callback for a finished job.

    Kept intentionally small for the MVP: successful jobs may drive a lifecycle
    update on their linked domain object. Extend per job_type as real pipelines
    land (e.g. ingest -> mark volume ready, generate_tasks -> create tasks).
    """
    # Placeholder hook; concrete per-job-type behaviour is future work.
    return None
