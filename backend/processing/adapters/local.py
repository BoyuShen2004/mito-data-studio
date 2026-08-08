"""Local processing backend.

Jobs without ``config.argv`` retain the original deterministic mock behaviour.
An upgrade deployment may opt into real local execution with a strict executable
allow-list. Commands are argv arrays and never pass through a shell.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings

from core.choices import ProcessingJobStatus
from ..interfaces import JobResult, ProcessingBackend


class LocalProcessingBackend(ProcessingBackend):
    name = "local"

    def submit(self, job) -> JobResult:
        output_dir = self._job_dir(job)
        argv = (job.config or {}).get("argv")
        if argv is not None:
            return self._execute(job, output_dir, argv)

        # Backward-compatible deterministic mock for development and existing
        # tests. Real pipelines must always declare argv explicitly.
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            marker = output_dir / "result.json"
            marker.write_text(
                f'{{"job": {job.id}, "type": "{job.job_type}", "backend": "local"}}'
            )
            output_paths = {"result": str(marker)}
            log_path = str(output_dir / "job.log")
            Path(log_path).write_text(f"local job {job.id} {job.job_type} ok\n")
        except OSError as exc:  # storage not writable -> fail cleanly
            return JobResult(
                status=ProcessingJobStatus.FAILED,
                error_message=f"Local backend could not write outputs: {exc}",
            )
        return JobResult(
            status=ProcessingJobStatus.SUCCEEDED,
            external_job_id=f"local-{job.id}",
            output_paths=output_paths,
            log_path=log_path,
            detail="Local mock run completed.",
        )

    def _execute(self, job, output_dir: Path, argv) -> JobResult:
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(value, str) and value for value in argv)
        ):
            return JobResult(
                status=ProcessingJobStatus.FAILED,
                error_message="config.argv must be a non-empty array of strings.",
            )
        executable = Path(argv[0]).name
        allowed = {
            value.strip()
            for value in settings.MITO_LOCAL_EXECUTABLE_ALLOWLIST.split(",")
            if value.strip()
        }
        if executable not in allowed:
            return JobResult(
                status=ProcessingJobStatus.FAILED,
                error_message=f"Local executable is not allow-listed: {executable}",
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = output_dir / "job.log"
        env = {
            key: value
            for key, value in os.environ.items()
            if key in settings.MITO_PROCESSING_ENV_ALLOWLIST
        }
        # PATH is required when argv[0] is a bare executable. It is supplied by
        # the operator, never by job config.
        if "PATH" in os.environ:
            env["PATH"] = os.environ["PATH"]
        try:
            # Stream output directly to disk. Scientific commands can emit
            # gigabytes of progress text; buffering it in the dispatcher would
            # make a successful model run capable of exhausting worker memory.
            with log_path.open("w") as log:
                completed = subprocess.run(
                    argv,
                    cwd=output_dir,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=settings.MITO_LOCAL_JOB_TIMEOUT_SECONDS,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            with log_path.open("a") as log:
                log.write(f"\nlocal execution failed: {exc}\n")
            return JobResult(
                status=ProcessingJobStatus.FAILED,
                log_path=str(log_path),
                error_message=f"Local execution failed: {exc}",
            )
        if completed.returncode != 0:
            return JobResult(
                status=ProcessingJobStatus.FAILED,
                external_job_id=f"local-{job.id}",
                log_path=str(log_path),
                error_message=f"Local command exited with status {completed.returncode}.",
            )

        artifacts = []
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file() or path.name in {"result.json", "job.log"}:
                continue
            digest_state = hashlib.sha256()
            with path.open("rb") as artifact:
                for block in iter(lambda: artifact.read(1024 * 1024), b""):
                    digest_state.update(block)
            digest = digest_state.hexdigest()
            artifacts.append(
                {
                    "path": str(path.relative_to(output_dir)),
                    "bytes": path.stat().st_size,
                    "sha256": digest,
                }
            )
        marker = output_dir / "result.json"
        marker.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "job": job.id,
                    "type": job.job_type,
                    "backend": "local",
                    "argv": argv,
                    "artifacts": artifacts,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return JobResult(
            status=ProcessingJobStatus.SUCCEEDED,
            external_job_id=f"local-{job.id}",
            output_paths={"result": str(marker), "output_dir": str(output_dir)},
            log_path=str(log_path),
            detail=f"Local command completed with {len(artifacts)} versioned artifact(s).",
        )

    def poll(self, job) -> JobResult:
        # Local jobs finish on submit, so polling just echoes the current state.
        return JobResult(status=job.status, external_job_id=job.external_job_id)

    def cancel(self, job) -> JobResult:
        return JobResult(
            status=ProcessingJobStatus.CANCELLED,
            external_job_id=job.external_job_id,
            detail="Local job cancelled.",
        )

    @staticmethod
    def _job_dir(job) -> Path:
        root = Path(getattr(settings, "MITO_SHARED_STORAGE_ROOT", settings.MITO_DATA_ROOT))
        return root / "processing_jobs" / str(job.id)
