from django.core.management.base import BaseCommand

from accounts.roles import get_role
from accounts.teams import ensure_project_assignee_eligible
from core.choices import UserRole
from projects.models import Project, ProjectMembership


class Command(BaseCommand):
    help = (
        "Preview or apply synchronization of explicit project annotators into "
        "each project's working team. Dry-run is the default."
    )

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=int)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply changes. Without this flag the command only reports them.",
        )

    def handle(self, *args, **options):
        projects = Project.objects.all().order_by("id")
        if options["project_id"]:
            projects = projects.filter(pk=options["project_id"])
        pending = 0
        for project in projects:
            memberships = ProjectMembership.objects.filter(project=project).select_related(
                "user", "user__profile"
            )
            for access in memberships:
                user = access.user
                if get_role(user) != UserRole.ANNOTATOR:
                    continue
                eligible = bool(
                    project.working_team_id
                    and project.working_team.memberships.filter(user=user).exists()
                )
                if eligible:
                    continue
                pending += 1
                self.stdout.write(
                    f"project={project.pk} user={user.pk}:{user.get_username()} -> working team"
                )
                if options["apply"]:
                    ensure_project_assignee_eligible(project, user, actor=None)
                    project.refresh_from_db(fields=["working_team"])
        mode = "applied" if options["apply"] else "would apply"
        self.stdout.write(self.style.SUCCESS(f"{mode}: {pending} roster membership(s)"))
