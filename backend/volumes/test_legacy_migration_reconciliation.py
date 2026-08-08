from django.core.management import call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase

from volumes.management.commands.reconcile_legacy_region_mask_migration import (
    LEGACY_MIGRATION,
    RELEASE_MIGRATION,
    validate_region_mask_columns,
)


class LegacyRegionMaskMigrationReconciliationTests(TestCase):
    def test_release_schema_has_exact_legacy_compatible_columns(self):
        self.assertEqual(validate_region_mask_columns(), [])

    def test_command_records_only_the_equivalent_migration(self):
        recorder = MigrationRecorder(connection)
        recorder.record_unapplied(*RELEASE_MIGRATION)
        recorder.record_applied(*LEGACY_MIGRATION)

        call_command("reconcile_legacy_region_mask_migration")
        self.assertNotIn(RELEASE_MIGRATION, recorder.applied_migrations())

        call_command("reconcile_legacy_region_mask_migration", apply=True)
        applied = recorder.applied_migrations()
        self.assertIn(LEGACY_MIGRATION, applied)
        self.assertIn(RELEASE_MIGRATION, applied)
