from django.core.management.base import BaseCommand, CommandError

from annotation.services import assign_tasks_rule_based
from projects.models import Project


class Command(BaseCommand):
    help = "Rule-based assignment of unassigned tasks to active annotators."

    def add_arguments(self, parser):
        parser.add_argument(
            "--project-id",
            type=int,
            default=None,
            help="Restrict assignment to one project (default: all projects).",
        )

    def handle(self, *args, **options):
        project = None
        if options["project_id"] is not None:
            try:
                project = Project.objects.get(pk=options["project_id"])
            except Project.DoesNotExist:
                raise CommandError(f"Project {options['project_id']} does not exist")

        result = assign_tasks_rule_based(project=project)
        self.stdout.write(
            self.style.SUCCESS(
                f"Assigned {result['assigned']} task(s); "
                f"{result['remaining_unassigned']} still unassigned."
            )
        )
