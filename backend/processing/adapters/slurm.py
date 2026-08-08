"""SLURM processing backend.

Encapsulates sbatch submission, squeue/sacct polling, scancel, log paths, and
result collection. All cluster-specific values (partition, account, binaries)
come from settings/environment — nothing lab-specific is hard-coded, and no
real HPC job needs to succeed in local development.

The heavy per-job-type command is written by the caller into
``job.config['command']`` (or ``job.config['sbatch_script']``); this adapter is
responsible only for talking to SLURM, not for the science.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from django.conf import settings

from core.choices import ProcessingJobStatus
from ..interfaces import JobResult, ProcessingBackend

# Maps SLURM state codes (from ``sacct``/``squeue``) to our job statuses.
_SLURM_STATE_MAP = {
    "PENDING": ProcessingJobStatus.SUBMITTED,
    "CONFIGURING": ProcessingJobStatus.SUBMITTED,
    "RUNNING": ProcessingJobStatus.RUNNING,
    "COMPLETING": ProcessingJobStatus.RUNNING,
    "COMPLETED": ProcessingJobStatus.SUCCEEDED,
    "FAILED": ProcessingJobStatus.FAILED,
    "TIMEOUT": ProcessingJobStatus.FAILED,
    "NODE_FAIL": ProcessingJobStatus.FAILED,
    "OUT_OF_MEMORY": ProcessingJobStatus.FAILED,
    "BOOT_FAIL": ProcessingJobStatus.FAILED,
    "CANCELLED": ProcessingJobStatus.CANCELLED,
}


class SlurmProcessingBackend(ProcessingBackend):
    name = "slurm"

    def submit(self, job) -> JobResult:
        Path(self._log_path(job, "pending")).parent.mkdir(parents=True, exist_ok=True)
        script = self._resolve_script(job)
        if not script:
            return JobResult(
                status=ProcessingJobStatus.FAILED,
                error_message=(
                    "No sbatch script/command in job.config; cannot submit to SLURM."
                ),
            )
        cmd = [settings.MITO_SLURM_SBATCH, *self._sbatch_options(job), script]
        try:
            out = self._run(cmd)
        except (OSError, subprocess.CalledProcessError) as exc:
            return JobResult(
                status=ProcessingJobStatus.FAILED,
                error_message=f"sbatch failed: {exc}",
            )
        job_id = self._parse_sbatch_job_id(out)
        return JobResult(
            status=ProcessingJobStatus.SUBMITTED,
            external_job_id=job_id,
            log_path=self._log_path(job, job_id),
            detail=f"Submitted to SLURM as job {job_id}.",
        )

    def poll(self, job) -> JobResult:
        if not job.external_job_id:
            return JobResult(status=job.status)
        cmd = [
            settings.MITO_SLURM_SACCT,
            "-j",
            job.external_job_id,
            "--noheader",
            "-P",
            "-o",
            "State",
        ]
        try:
            out = self._run(cmd)
        except (OSError, subprocess.CalledProcessError) as exc:
            return JobResult(
                status=job.status, error_message=f"sacct failed: {exc}"
            )
        state = out.strip().splitlines()[0].split("|")[0].strip() if out.strip() else ""
        state = re.sub(r"\s+.*$", "", state)  # e.g. "CANCELLED by 123"
        return JobResult(
            status=_SLURM_STATE_MAP.get(state, job.status),
            external_job_id=job.external_job_id,
        )

    def cancel(self, job) -> JobResult:
        if not job.external_job_id:
            return JobResult(status=ProcessingJobStatus.CANCELLED)
        try:
            self._run([settings.MITO_SLURM_SCANCEL, job.external_job_id])
        except (OSError, subprocess.CalledProcessError) as exc:
            return JobResult(status=job.status, error_message=f"scancel failed: {exc}")
        return JobResult(
            status=ProcessingJobStatus.CANCELLED,
            external_job_id=job.external_job_id,
        )

    # --- helpers -----------------------------------------------------------
    def _sbatch_options(self, job) -> list[str]:
        opts: list[str] = []
        if settings.MITO_SLURM_PARTITION:
            opts += ["--partition", settings.MITO_SLURM_PARTITION]
        if settings.MITO_SLURM_ACCOUNT:
            opts += ["--account", settings.MITO_SLURM_ACCOUNT]
        opts += ["--job-name", f"mito-{job.job_type}-{job.id}"]
        log_path = self._log_path(job, "%j")
        opts += ["--output", log_path]
        return opts

    @classmethod
    def _resolve_script(cls, job) -> str:
        cfg = job.config or {}
        if cfg.get("sbatch_script") or cfg.get("command"):
            return cfg.get("sbatch_script") or cfg.get("command") or ""
        argv = cfg.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(value, str) and value for value in argv)
        ):
            return ""
        # Generate a private, auditable script from an argv array. shlex.join
        # preserves argument boundaries; arbitrary hand-written shell remains
        # possible only through the explicit legacy `sbatch_script` field.
        root = Path(cls._log_path(job, "pending")).parent
        root.mkdir(parents=True, exist_ok=True)
        script = root / "submit.sh"
        script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + shlex.join(argv) + "\n")
        script.chmod(0o700)
        return str(script)

    @staticmethod
    def _log_path(job, job_id) -> str:
        root = Path(getattr(settings, "MITO_SHARED_STORAGE_ROOT", settings.MITO_DATA_ROOT))
        return str(root / "processing_jobs" / str(job.id) / f"slurm-{job_id}.log")

    @staticmethod
    def _parse_sbatch_job_id(output: str) -> str:
        # "Submitted batch job 123456"
        match = re.search(r"(\d+)", output or "")
        return match.group(1) if match else ""

    @staticmethod
    def _run(cmd: list[str]) -> str:
        return subprocess.run(
            cmd, check=True, capture_output=True, text=True
        ).stdout
