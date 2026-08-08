from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.migrations.recorder import MigrationRecorder


LEGACY_MIGRATION = ("volumes", "0005_volume_region_mask")
RELEASE_MIGRATION = ("volumes", "0006_volume_region_mask")
EXPECTED_COLUMNS = {
    "region_mask_file": {
        "data_type": "character varying",
        "maximum_length": 100,
        "nullable": True,
    },
    "region_mask_path": {
        "data_type": "character varying",
        "maximum_length": 1024,
        "nullable": False,
    },
}


def validate_region_mask_columns() -> list[str]:
    """Return precise schema mismatches without reading any application rows."""

    if connection.vendor != "postgresql":
        return [
            "legacy reconciliation is supported only for the PostgreSQL "
            f"deployment database, not {connection.vendor}"
        ]
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name, data_type, character_maximum_length, is_nullable
              FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name = 'volumes_volume'
               AND column_name IN ('region_mask_file', 'region_mask_path')
            """
        )
        columns = {
            name: {
                "data_type": data_type,
                "maximum_length": maximum_length,
                "nullable": nullable == "YES",
            }
            for name, data_type, maximum_length, nullable in cursor.fetchall()
        }
    errors: list[str] = []
    for name, expected in EXPECTED_COLUMNS.items():
        actual_column = columns.get(name)
        if actual_column is None:
            errors.append(f"missing column volumes_volume.{name}")
            continue
        for attribute, value in expected.items():
            actual = actual_column[attribute]
            if actual != value:
                errors.append(
                    f"volumes_volume.{name} {attribute}={actual!r}, expected {value!r}"
                )
    return errors


class Command(BaseCommand):
    help = (
        "Reconcile the active deployment's equivalent 0005 region-mask schema "
        "with the release migration graph. No application rows are changed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Record the equivalent release migration after all guards pass.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        recorder = MigrationRecorder(connection)
        applied = recorder.applied_migrations()
        if RELEASE_MIGRATION in applied:
            self.stdout.write("Release region-mask migration is already recorded.")
            return
        if LEGACY_MIGRATION not in applied:
            raise CommandError(
                "Legacy volumes.0005_volume_region_mask is not recorded; "
                "run the normal migration path instead."
            )

        errors = validate_region_mask_columns()
        if errors:
            raise CommandError("Schema guard failed: " + "; ".join(errors))

        if not options["apply"]:
            self.stdout.write(
                "Legacy region-mask schema is exactly compatible; dry-run only."
            )
            return

        recorder.record_applied(*RELEASE_MIGRATION)
        self.stdout.write(
            self.style.SUCCESS(
                "Recorded volumes.0006_volume_region_mask as the exact equivalent "
                "of deployed volumes.0005_volume_region_mask; no data changed."
            )
        )
