"""Run the ProcessingJob dispatcher.

A deliberately simple loop (no Celery/Redis): claim queued jobs, submit them
through the configured backend, poll active ones, and record results — all via
the ``processing.services`` layer. Run once (``--once``) or continuously.

    python manage.py run_processing_dispatcher            # loop
    python manage.py run_processing_dispatcher --once     # single pass
    python manage.py run_processing_dispatcher --interval 10
"""

import time

from django.core.management.base import BaseCommand

from processing.services import run_dispatch_once


class Command(BaseCommand):
    help = "Submit queued processing jobs and poll active ones."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run a single pass.")
        parser.add_argument(
            "--interval",
            type=float,
            default=5.0,
            help="Seconds between passes in loop mode (default 5).",
        )
        parser.add_argument(
            "--max-new",
            type=int,
            default=10,
            help="Max queued jobs to submit per pass (default 10).",
        )
        parser.add_argument(
            "--job-type",
            action="append",
            dest="job_types",
            help="Only dispatch this job type (repeatable). Default: all types.",
        )

    def handle(self, *args, **options):
        once = options["once"]
        interval = options["interval"]
        max_new = options["max_new"]
        job_types = tuple(options["job_types"] or ()) or None

        while True:
            summary = run_dispatch_once(max_new=max_new, job_types=job_types)
            if summary["submitted"] or summary["polled"]:
                self.stdout.write(
                    f"submitted={summary['submitted']} polled={summary['polled']}"
                )
            if once:
                break
            time.sleep(interval)
