"""The pyramid build as a job (doc 20 §Pyramid job).

    ProcessingJob(type=build_pyramid) → Slurm/local → write derivative →
    validate random chunk checksums → mark Volume ready_streaming=true

Phase 11 built the middle three steps; this wires the first one, so the build is
submittable rather than only callable from Python.

**Scope note on Slurm.** Doc 20 says "Slurm/local", and a `ProcessingBackend`
protocol with local and Slurm adapters already exists from earlier phases. The
phase map, however, puts **HPC/Slurm integration at row 18** (depends on 17,
gate "end-to-end predict"), and row 12's gate is `authz + metrics`. So this
module wires the build onto the *existing* backend abstraction — which the Slurm
adapter already implements — and does **not** build or extend Slurm support.
Selecting the `slurm` backend routes through the same protocol; making that path
production-ready is row 18's job, and no test submits to a real scheduler.

Separation kept explicit, per the standing rule:

    specification   PyramidBuildSpec — a plain dataclass, no scheduler concepts
    persistence     the existing ProcessingJob row
    runner adapter  processing.registry.get_processing_backend()
    execution       volumes.pyramid.service.build_pyramid
    registration    the service validates and promotes; this records the outcome
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.choices import ProcessingJobStatus, ProcessingJobType

from . import service as pyramid_service

logger = logging.getLogger(__name__)


class PyramidJobError(RuntimeError):
    def __init__(self, message: str, *, reason: str = "invalid"):
        super().__init__(message)
        self.reason = reason


class DuplicateBuild(PyramidJobError):
    """An active build already exists for this volume."""

    def __init__(self, message: str, *, job_id=None):
        super().__init__(message, reason="duplicate_build")
        self.job_id = job_id


@dataclass(frozen=True)
class PyramidBuildSpec:
    """What to build. Deliberately free of scheduler concepts.

    A specification that mentioned partitions or sbatch flags would make the
    algorithm depend on Slurm, which doc 20's own topology forbids.

    ``layer`` selects the image or region derivative. One job *type* covers
    both — a second `ProcessingJobType` would fork the dispatcher allow-list,
    the metrics and the status UI for what is one build with a different source
    and reduction. ``is_label`` stays ``None`` by default so the layer picks
    the reduction (mode for region), which is the choice that must not be got
    wrong silently.
    """

    volume_id: int
    layer: str = "image"
    is_label: bool | None = None
    samples_per_level: int = 4
    seed: int = 20260730
    idempotency_key: str = ""
    metadata: dict = field(default_factory=dict)

    def as_payload(self) -> dict:
        return {
            "volume_id": self.volume_id,
            "layer": self.layer,
            "is_label": self.is_label,
            "samples_per_level": self.samples_per_level,
            "seed": self.seed,
            "idempotency_key": self.idempotency_key,
            **self.metadata,
        }


def job_layer(job) -> str:
    """The layer a job builds. Jobs predating layers are image builds."""
    return str((job.config or {}).get("layer") or "image")


# Queued *and* claimed count as active. The dispatcher marks a job SUBMITTED
# before it hands it to a backend, so omitting that state let a second submit
# slip past the duplicate guard in exactly the window where a build is about to
# start — a race that is invisible with the local backend and permanent with a
# scheduler, where SUBMITTED is where a job spends most of its life.
ACTIVE_STATUSES = (
    ProcessingJobStatus.QUEUED,
    ProcessingJobStatus.SUBMITTED,
    ProcessingJobStatus.RUNNING,
)


def _active_build(volume_id: int, layer: str = "image"):
    from processing.models import ProcessingJob

    # Filtered on the real FK rather than a JSON lookup: the relation exists on
    # the model, and an indexed column beats a JSON scan. The layer is then
    # matched in Python over that handful of rows — a `config__layer` filter
    # would miss every job written before layers existed, whose payload has no
    # such key but which is an image build.
    active = ProcessingJob.objects.filter(
        job_type=ProcessingJobType.BUILD_PYRAMID,
        status__in=ACTIVE_STATUSES,
        volume_id=volume_id,
    ).order_by("-id")
    for job in active:
        if job_layer(job) == layer:
            return job
    return None


def _existing_for_key(key: str):
    """The *unfinished* build already submitted under ``key``, if any.

    An idempotency key deduplicates a repeated request; it does not retire the
    volume forever. Replaying a **terminal** job would mean a mask replaced
    after its first build silently got the old job handed back instead of a new
    build — the derivative cleared, nothing queued, and the ROI stuck at "Not
    built" with no way back short of a manual click. So only work that is still
    in flight replays; anything succeeded, failed or cancelled is history, and a
    caller asking again is asking for a new build.
    """
    from processing.models import ProcessingJob

    if not key:
        return None
    return (
        ProcessingJob.objects.filter(
            job_type=ProcessingJobType.BUILD_PYRAMID,
            status__in=ACTIVE_STATUSES,
            config__idempotency_key=key,
        )
        .order_by("-id")
        .first()
    )


def submit_build(spec: PyramidBuildSpec, *, actor=None, backend_name: str | None = None):
    """Create (or replay) a build job. Returns the ``ProcessingJob``.

    Refuses a second concurrent build for the same volume: two builds writing one
    derivative would interleave, and Phase 11's promote-after-validate protects
    the *store*, not the wasted work.
    """
    if not pyramid_service.pyramids_enabled():
        raise PyramidJobError(
            "Volume pyramids are not enabled on this instance.", reason="disabled"
        )

    from processing.models import ProcessingJob

    replay = _existing_for_key(spec.idempotency_key)
    if replay is not None:
        return replay

    with transaction.atomic():
        active = _active_build(spec.volume_id, spec.layer)
        if active is not None:
            raise DuplicateBuild(
                f"A {spec.layer} pyramid build is already active for volume "
                f"{spec.volume_id}.",
                job_id=active.pk,
            )
        from volumes.models import Volume

        volume = Volume.objects.select_related("project").get(pk=spec.volume_id)
        job = ProcessingJob.objects.create(
            job_type=ProcessingJobType.BUILD_PYRAMID,
            status=ProcessingJobStatus.QUEUED,
            backend=backend_name
            or getattr(settings, "MITO_PROCESSING_BACKEND", "local"),
            volume=volume,
            project=volume.project,
            config=spec.as_payload(),
            created_by=actor,
        )

    return job


def run_build(job) -> dict:
    """Execute a queued build in-process and record the outcome.

    This is the *execution* step, kept separate from submission so a runner
    adapter can call it without knowing how the job was created. It performs no
    scheduler work of its own.
    """
    from processing.models import ProcessingJob
    from volumes.models import Volume

    params = job.config or {}
    volume_id = params.get("volume_id") or job.volume_id
    if volume_id is None:
        raise PyramidJobError("Job has no volume_id.", reason="bad_spec")
    layer = job_layer(job)

    ProcessingJob.objects.filter(pk=job.pk).update(
        status=ProcessingJobStatus.RUNNING,
        started_at=timezone.now(),
    )
    job.refresh_from_db(fields=["status", "started_at"])

    # A region job whose mask was removed between submit and run has nothing to
    # build. That is an accurate absence, not a failure: recording it as FAILED
    # would put a red status on a volume that is simply image-only.
    if layer == "region" and not Volume.objects.filter(
        pk=volume_id
    ).exclude(region_mask_path="", region_mask_file="").exists():
        outcome = {"volume_id": volume_id, "layer": layer, "skipped": "no_region_mask"}
        ProcessingJob.objects.filter(pk=job.pk).update(
            status=ProcessingJobStatus.SUCCEEDED,
            output_paths={},
            finished_at=timezone.now(),
        )
        job.refresh_from_db()
        return outcome

    try:
        volume = Volume.objects.get(pk=volume_id)
        report = pyramid_service.build_pyramid(
            volume,
            layer=layer,
            is_label=params.get("is_label"),
            samples_per_level=int(params.get("samples_per_level", 4)),
            seed=int(params.get("seed", 20260730)),
        )
        # Coverage requires a complete scan of the ROI.  It used to happen in
        # register_volume(), keeping the HTTP request open for minutes and
        # causing Cloudflare 524s.  A region pyramid is already background
        # work, so calculate this additive fact here after the derivative has
        # been validated.  Coverage failure must not invalidate a good pyramid.
        if layer == "region":
            try:
                from volumes.region_masks import refresh_region_mask_coverage

                refresh_region_mask_coverage(volume)
            except Exception:
                logger.exception(
                    "Could not calculate region-mask coverage for volume %s",
                    volume_id,
                )
    except Exception as exc:
        ProcessingJob.objects.filter(pk=job.pk).update(
            status=ProcessingJobStatus.FAILED,
            error_message=str(exc)[:500],
            finished_at=timezone.now(),
        )
        job.refresh_from_db()
        # Phase 11 already discarded the partial build and left the previous
        # derivative in place; nothing to clean up here.
        raise

    # A rebuild replaces the store the chunk service may hold a handle to.
    # Invalidation is derived from build identity, but dropping the handle here
    # makes the transition immediate rather than merely eventual.
    try:
        from volumes.chunks import service as chunk_service

        chunk_service.invalidate_volume(volume_id, layer=report.layer)
    except Exception:  # pragma: no cover - chunk service is optional
        pass

    outcome = report.as_dict()
    ProcessingJob.objects.filter(pk=job.pk).update(
        status=ProcessingJobStatus.SUCCEEDED,
        # One key for both layers: a job row builds exactly one layer, and its
        # `config["layer"]` already says which.
        output_paths={"pyramid": report.path},
        finished_at=timezone.now(),
    )
    job.refresh_from_db()
    return outcome


def cancel_build(job) -> bool:
    """Cancel a queued build. A running in-process build cannot be interrupted.

    Said plainly rather than pretended: the local backend runs the build inline,
    so there is no process to signal. Cancelling a *queued* job prevents it from
    starting, which is the honest extent of the guarantee here.
    """
    from processing.models import ProcessingJob

    if job.status != ProcessingJobStatus.QUEUED:
        return False
    ProcessingJob.objects.filter(pk=job.pk, status=ProcessingJobStatus.QUEUED).update(
        status=ProcessingJobStatus.CANCELLED
    )
    job.refresh_from_db(fields=["status"])
    return True


def job_status(job) -> dict:
    """Safe status payload — identifiers and state, never a filesystem path."""
    params = job.config or {}
    return {
        "job_id": job.pk,
        "status": job.status,
        "job_type": job.job_type,
        "layer": job_layer(job),
        "volume_id": params.get("volume_id") or job.volume_id,
        "error": (job.error_message or "")[:500],
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }
