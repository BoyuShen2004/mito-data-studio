"""One-shot local reset: clear data, apply migrations, reseed accounts.

    python manage.py reset_dev

Convenience wrapper around ``clear_dev_data`` + ``migrate`` + ``seed_dev`` for a
clean, reproducible development database (standard accounts, no data). Use
``--no-migrate`` to skip migrations.
"""

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Reset the dev database: clear data, migrate, and reseed standard mock data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-migrate",
            action="store_true",
            help="Skip running migrations.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow running even when DEBUG is False.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to reset with DEBUG=False. Re-run with --force if you "
                "really mean it."
            )

        self.stdout.write(self.style.MIGRATE_HEADING("1/3 Clearing data"))
        call_command(
            "clear_dev_data",
            "--no-input",
            *(["--force"] if options["force"] else []),
        )

        if not options["no_migrate"]:
            self.stdout.write(self.style.MIGRATE_HEADING("2/3 Applying migrations"))
            call_command("migrate")
        else:
            self.stdout.write("2/3 Skipping migrations (--no-migrate)")

        self.stdout.write(self.style.MIGRATE_HEADING("3/3 Seeding accounts"))
        call_command("seed_dev")

        self.stdout.write(self.style.SUCCESS("Development environment reset."))
