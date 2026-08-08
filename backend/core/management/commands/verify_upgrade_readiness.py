"""Read-only preflight for the integrated WEBKNOSSOS upgrade profile."""

from __future__ import annotations

import json

from django.conf import settings
from django.core.checks import run_checks
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count, Q


class Command(BaseCommand):
    help = "Verify schema, backfills and feature dependencies before upgrade traffic."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero for blockers (recommended in release gates).",
        )

    def handle(self, *args, **options):
        from accounts.models import Institution, Team
        from annotation.models import AnnotationTask
        from volumes.models import Volume

        executor = MigrationExecutor(connection)
        pending_migrations = executor.migration_plan(executor.loader.graph.leaf_nodes())
        orgs_without_default_team = (
            Institution.objects.annotate(
                default_team_count=Count("teams", filter=Q(teams__is_default=True))
            )
            .filter(default_team_count=0)
            .count()
        )
        # Explicitly count the derivative rollout. It is informational: source
        # TIFF/PNG fallback remains supported while pyramids build in batches.
        volumes = Volume.objects.count()
        streaming = Volume.objects.filter(ready_streaming=True).count()

        check_issues = run_checks(tags=["deployment"])
        errors = [issue for issue in check_issues if issue.is_serious()]
        blockers = {
            "pending_migrations": len(pending_migrations),
            "organizations_without_default_team": orgs_without_default_team,
            "system_check_errors": len(errors),
        }
        report = {
            "profile": settings.MITO_UPGRADE_PROFILE,
            "database_engine": connection.vendor,
            "blockers": blockers,
            "inventory": {
                "organizations": Institution.objects.count(),
                "teams": Team.objects.count(),
                "tasks": AnnotationTask.objects.count(),
                "volumes": volumes,
                "streaming_ready_volumes": streaming,
                "streaming_rollout_complete": volumes == streaming,
            },
            "ready": not any(blockers.values()),
        }
        rendered = json.dumps(report, indent=2, sort_keys=True)
        self.stdout.write(rendered)
        if options["strict"] and not report["ready"]:
            raise CommandError("Upgrade readiness blockers remain.")
