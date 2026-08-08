from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.application_reset import (
    CONFIRM_PHRASE, ResetRefused, execute_reset, issue_confirmation,
    row_summary, storage_manifest,
)


class Command(BaseCommand):
    help = "Dry-run or execute the guarded fresh-application reset"

    def add_arguments(self, parser):
        parser.add_argument("--issue-confirmation", action="store_true")
        parser.add_argument("--confirm", default="")
        parser.add_argument("--phrase", default="")
        parser.add_argument("--admin", default="admin")

    def handle(self, *args, **options):
        self.stdout.write(f"rows={row_summary()}")
        self.stdout.write(f"storage={storage_manifest()}")
        User = get_user_model()
        try:
            admin = User.objects.get(username=options["admin"], is_superuser=True)
            if options["issue_confirmation"]:
                token = issue_confirmation(admin, options["phrase"])
                self.stdout.write(self.style.WARNING(f"confirmation_token={token}"))
                return
            if options["confirm"]:
                result = execute_reset(admin, options["confirm"], options["phrase"])
                self.stdout.write(self.style.SUCCESS(f"reset={result}"))
                return
        except (User.DoesNotExist, ResetRefused) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Dry run only. Re-run with --issue-confirmation --phrase '{CONFIRM_PHRASE}'.")
