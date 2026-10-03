"""Read-only label measurements executed by the existing job dispatcher."""
import json
import logging
from dataclasses import asdict
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from annotation.label_paths import working_label_rel_path
from annotation.measurement_spacing import measurement_spacing
from annotation.measurements import DUST_SIZE, MeasurementError, measure_label_volume, validate_voxel_size
from annotation.visualization.slice_io import resolve_path
from core.choices import ProcessingJobStatus
from processing.interfaces import JobResult

MAX_CROP_VOXELS = 8_000_000
logger = logging.getLogger(__name__)


def measurement_input(volume, source):
    if source not in ("official", "working"):
        raise MeasurementError("Choose official or working labels.")
    spacing_um = measurement_spacing(volume)["voxel_size_um_zyx"]
    spacing = validate_voxel_size([value * 1000 if value is not None else None for value in spacing_um])
    if source == "official" and not volume.has_label:
        raise MeasurementError("No official label is registered for this volume.")
    path = resolve_path(working_label_rel_path(volume) if source == "working" else volume.label_location)
    if not path.is_file():
        raise MeasurementError("No saved working label file exists." if source == "working" else "The official label file is unavailable.")
    stat = path.stat()
    return {
        "source": source,
        "voxel_size_nm_zyx": list(spacing),
        "path": str(path),
        "file_version": [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns],
    }


def input_is_current(job):
    if job.volume_id is None:
        return False
    try:
        return measurement_input(job.volume, job.config["source"]) == job.config["input"]
    except (MeasurementError, OSError):
        return False


def run_measurement(job):
    try:
        job.volume.refresh_from_db()
        if not input_is_current(job):
            raise MeasurementError("Labels or voxel size changed after this run was queued. Run measurements again.")
        rows = measure_label_volume(
            job.config["input"]["path"], job.config["input"]["voxel_size_nm_zyx"],
            max_crop_voxels=MAX_CROP_VOXELS,
        )
        job.volume.refresh_from_db()
        if not input_is_current(job):
            raise MeasurementError("Labels or voxel size changed during measurement. Results were discarded; run again after saving finishes.")
        job.refresh_from_db(fields=["status"])
        if job.status == ProcessingJobStatus.CANCELLED:
            return JobResult(status=ProcessingJobStatus.CANCELLED)
        result = {
            "rows": [asdict(row) for row in rows],
            "measured_at": timezone.now().isoformat(),
            "source": job.config["source"],
            "voxel_size_nm_zyx": job.config["input"]["voxel_size_nm_zyx"],
            "dust_size_voxels": DUST_SIZE,
            "method": "TEASAR (kimimaro)",
            "scope": "Whole label volume; no task-range or ROI clipping.",
        }
        directory = Path(settings.MITO_DATA_ROOT) / "processing_jobs" / str(job.pk)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "mitochondria.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, allow_nan=False))
        temporary.replace(path)
        return JobResult(status=ProcessingJobStatus.SUCCEEDED, output_paths={"measurements": str(path)})
    except MeasurementError as exc:
        return JobResult(status=ProcessingJobStatus.FAILED, error_message=str(exc))
    except Exception:
        logger.exception("Mitochondria measurement job %s failed", job.pk)
        return JobResult(status=ProcessingJobStatus.FAILED, error_message="Measurement failed. Check the label file and dispatcher log, then retry.")
