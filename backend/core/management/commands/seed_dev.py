"""Create the standard development accounts.

    python manage.py seed_dev

Creates one manager, four annotator and two requester accounts (password
"demo12345"). It does **not** register any datasets, volumes, or tasks —
developers register data manually through the app.

Safe to run repeatedly. Use ``--fresh`` to wipe existing development data first.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from core.dev_data import DEMO_PASSWORD, seed_standard_data


class Command(BaseCommand):
    help = "Create the standard development accounts (manager, annotators, requesters; no data)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fresh",
            action="store_true",
            help="Clear existing development data before seeding accounts.",
        )
        parser.add_argument(
            "--safe-mock-login",
            action="store_true",
            help=(
                "Create allowlisted click-to-fill accounts using the configured "
                "MOCK_DEV_LOGIN_PASSWORD. The manager keeps its application "
                "role but is not staff or a superuser."
            ),
        )

    def handle(self, *args, **options):
        if options["fresh"]:
            call_command("clear_dev_data", "--no-input")

        safe_mock_login = options["safe_mock_login"]
        result = seed_standard_data(
            log=self.stdout.write, safe_mock_login=safe_mock_login
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Ready: {len(result['managers'])} manager, "
                f"{len(result['annotators'])} annotator, "
                f"{len(result['requesters'])} requester account(s)."
            )
        )
        credential_note = (
            " · use the gated click-to-fill account selector"
            if safe_mock_login
            else " · password " + self.style.WARNING(DEMO_PASSWORD)
        )
        self.stdout.write(
            "Manager: "
            + ", ".join(result["managers"])
            + " · Annotators: "
            + ", ".join(result["annotators"])
            + " · Requesters: "
            + ", ".join(result["requesters"])
            + credential_note
        )
        self.stdout.write(
            "No data is pre-registered — register datasets manually in the app."
        )
