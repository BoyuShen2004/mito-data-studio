"""Repeatable v1.1 migration entrypoint for empty and legacy databases."""

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

from volumes.management.commands.reconcile_legacy_region_mask_migration import (
    LEGACY_MIGRATION,
    RELEASE_MIGRATION,
)


class Command(BaseCommand):
    help = (
        "Apply the complete v1.1 migration graph, safely reconciling the "
        "production-only legacy region-mask migration when present."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--plan",
            action="store_true",
            help="Show the normal Django plan and legacy reconciliation state only.",
        )

    def handle(self, *args, **options):
        applied = MigrationRecorder(connection).applied_migrations()
        legacy = LEGACY_MIGRATION in applied
        release = RELEASE_MIGRATION in applied

        self.stdout.write(
            f"region-mask migration state: legacy={legacy} release={release}"
        )
        if options["plan"]:
            if legacy and not release:
                call_command("reconcile_legacy_region_mask_migration")
            call_command("migrate", plan=True)
            return

        if legacy and not release:
            # These two migrations were released after the production-only
            # 0005 name had already been applied. Land their independent schema
            # first, prove the legacy columns are identical, then record the
            # graph-equivalent 0006 through the guarded command. Every step is
            # idempotent and contains no application-row edits.
            call_command(
                "migrate", "processing", "0002_phase11_build_pyramid_job_type",
                interactive=False,
            )
            call_command(
                "migrate", "volumes", "0005_phase11_volume_pyramid",
                interactive=False,
            )
            call_command("reconcile_legacy_region_mask_migration")
            call_command("reconcile_legacy_region_mask_migration", apply=True)

        call_command("migrate", interactive=False)
        self.stdout.write(self.style.SUCCESS("v1.1 migration sequence complete."))
