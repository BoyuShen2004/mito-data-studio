"""Invoke one auto-fill scheduler tick.

Deliberately thin. ADR-002 keeps the scheduling *algorithm* (a pure service
function in ``annotation.scheduler``) separate from the *mechanism* that
invokes it, so adopting a real task queue later is a change of caller and not a
rewrite of the logic. Cron or a systemd timer is the intended mechanism today:

    */5 * * * *  cd /srv/mito/backend && python manage.py auto_fill

    python manage.py auto_fill --dry-run          # propose, change nothing
    python manage.py auto_fill --project 3 --limit 50
    python manage.py auto_fill --tick-key nightly-2026-07-28   # idempotent
"""

from django.core.management.base import BaseCommand, CommandError

from annotation.scheduler import SchedulerError, run_auto_fill, scheduler_enabled
from projects.models import Project


class Command(BaseCommand):
    help = "Run one auto-fill scheduler tick (Phase 4)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Compute and record the plan without assigning anything.",
        )
        parser.add_argument(
            "--project", type=int, default=None,
            help="Restrict the tick to one project id.",
        )
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Cap assignments this tick (still bounded by "
                 "MITO_SCHEDULER_MAX_BATCH).",
        )
        parser.add_argument(
            "--tick-key", default=None,
            help="Idempotency handle. Re-running with the same key reports the "
                 "original result instead of assigning again.",
        )

    def handle(self, *args, **opts):
        if not scheduler_enabled():
            raise CommandError(
                "Auto-fill scheduler is disabled. Set FEATURE_AUTO_FILL_SCHEDULER=1 "
                "to enable it."
            )

        project = None
        if opts["project"] is not None:
            try:
                project = Project.objects.get(pk=opts["project"])
            except Project.DoesNotExist:
                raise CommandError(f"No project with id {opts['project']}.")

        try:
            result = run_auto_fill(
                project=project,
                dry_run=opts["dry_run"],
                limit=opts["limit"],
                tick_key=opts["tick_key"],
            )
        except SchedulerError as exc:
            raise CommandError(str(exc))

        if result.replayed:
            self.stdout.write(self.style.WARNING(
                f"tick {result.tick_key} already ran — reporting the original "
                f"result, nothing was assigned again"
            ))

        verb = "would assign" if result.mode == "dry_run" else "assigned"
        self.stdout.write(
            f"{verb} {len(result.proposals)} instance(s) "
            f"from {result.candidates_considered} candidate task(s) "
            f"across {result.users_available} available annotator(s) "
            f"in {result.duration_ms:.1f} ms"
        )
        for rec in result.proposals[:20]:
            self.stdout.write(
                f"  task {rec['task_id']} -> {rec['username']} "
                f"(score {rec['score']})"
            )
        if len(result.proposals) > 20:
            self.stdout.write(f"  ... and {len(result.proposals) - 20} more")

        if result.mode == "dry_run" and result.proposals:
            self.stdout.write(self.style.NOTICE(
                f"dry run — apply with SchedulerDecision id {result.decision_id}"
            ))
        self.stdout.write(self.style.SUCCESS("ok"))
