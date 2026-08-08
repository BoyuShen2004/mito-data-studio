"""Delete development data created during local development.

    python manage.py clear_dev_data            # prompts for confirmation
    python manage.py clear_dev_data --no-input  # skip the prompt
    python manage.py clear_dev_data --keep-users

Removes all projects, volumes, tasks, submissions, reviews, and institutions
(and the files the app stored for them). Non-superuser accounts are removed too
(superusers are always preserved). Guarded against non-DEBUG environments unless
``--force`` is given.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.dev_data import clear_dev_data


class Command(BaseCommand):
    help = "Clear development/mock data (projects, volumes, tasks, submissions, users)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Do not prompt for confirmation.",
        )
        parser.add_argument(
            "--keep-users",
            action="store_true",
            help="Keep user accounts (delete only project/annotation data).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow running even when DEBUG is False.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to clear data with DEBUG=False. Re-run with --force "
                "if you really mean it."
            )

        if not options["no_input"]:
            answer = input(
                "This deletes all development data (and non-superuser accounts). "
                "Type 'yes' to continue: "
            )
            if answer.strip().lower() != "yes":
                self.stdout.write("Aborted.")
                return

        clear_dev_data(
            keep_users=options["keep_users"],
            log=self.stdout.write,
        )
        self.stdout.write(self.style.SUCCESS("Development data cleared."))
