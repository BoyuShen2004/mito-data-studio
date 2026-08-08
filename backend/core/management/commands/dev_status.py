"""Print a quick summary of the current development data.

    python manage.py dev_status

Handy for confirming what is (or isn't) in the database without opening a shell.
"""

from django.core.management.base import BaseCommand

from core.dev_data import data_summary


class Command(BaseCommand):
    help = "Show counts of current development data (users, projects, tasks, …)."

    def handle(self, *args, **options):
        summary = data_summary()
        self.stdout.write(self.style.MIGRATE_HEADING("Development data summary"))
        width = max(len(k) for k in summary)
        for key, value in summary.items():
            self.stdout.write(f"  {key.ljust(width)}  {value}")
        if summary["users"] == 0:
            self.stdout.write(
                "\nNo accounts. Run "
                + self.style.WARNING("python manage.py seed_dev")
                + " to create the standard manager + annotator accounts."
            )
        elif summary["projects"] == 0:
            self.stdout.write(
                "\nNo registered data yet — register datasets manually in the app."
            )
