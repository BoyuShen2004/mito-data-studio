"""Manager auto-fill coverage for the canonical single-assignee model."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AnnotatorProfile, Institution, Team, UserProfile
from accounts.teams import (
    add_team_member,
    grant_project_team,
    revoke_project_team,
)
from annotation.models import AnnotationTask, SchedulerDecision
from annotation.scheduler import SchedulerError, run_auto_fill, scheduler_enabled
from core.choices import TaskStatus, UserRole
from projects.models import Dataset, Project
from volumes.models import Volume


SCHEDULER_ON = override_settings(
    FEATURE_AUTO_FILL_SCHEDULER=True,
    MITO_SCHEDULER_ACTIVE_DAYS=0,
)


class SchedulerFixture:
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user("scheduler-manager", password="x")
        UserProfile.objects.update_or_create(
            user=self.manager, defaults={"role": UserRole.MANAGER}
        )
        self.annotators = []
        for username in ("scheduler-a", "scheduler-b"):
            user = User.objects.create_user(username, password="x")
            UserProfile.objects.update_or_create(
                user=user, defaults={"role": UserRole.ANNOTATOR}
            )
            AnnotatorProfile.objects.update_or_create(
                user=user,
                defaults={"is_active_annotator": True, "max_active_tasks": 2},
            )
            self.annotators.append(user)
        self.project = Project.objects.create(
            title="Scheduler",
            created_by=self.manager,
            manager_reviewed=True,
            priority=5,
        )
        self.organization = Institution.objects.create(name="Scheduler base org")
        self.assignment_team = Team.objects.create(
            organization=self.organization, name="Scheduler assignment team"
        )
        for annotator in self.annotators:
            add_team_member(self.assignment_team, annotator)
        grant_project_team(self.project, self.assignment_team)
        self.dataset = Dataset.objects.create(project=self.project, name="data")
        self.volume = Volume.objects.create(
            project=self.project,
            dataset=self.dataset,
            name="volume",
            image_path="scheduler.tif",
        )

    def tasks(self, count=3):
        return [
            AnnotationTask.objects.create(
                project=self.project,
                volume=self.volume,
                z_start=i,
                z_end=i + 1,
                y_end=8,
                x_end=8,
                status=TaskStatus.UNASSIGNED,
                priority=count - i,
            )
            for i in range(count)
        ]


class SchedulerFlagTests(SchedulerFixture, TestCase):
    @override_settings(FEATURE_AUTO_FILL_SCHEDULER=False)
    def test_disabled_flag_refuses_without_mutation(self):
        self.tasks(1)
        self.assertFalse(scheduler_enabled())
        with self.assertRaises(SchedulerError):
            run_auto_fill(project=self.project)
        self.assertFalse(AnnotationTask.objects.exclude(assigned_to=None).exists())


@SCHEDULER_ON
class SchedulerAssignmentTests(SchedulerFixture, TestCase):
    def test_auto_fill_assigns_each_task_once_and_balances(self):
        self.tasks(3)
        result = run_auto_fill(project=self.project, actor=self.manager)
        self.assertEqual(result.assignments_made, 3)
        rows = list(self.project.tasks.order_by("id"))
        self.assertTrue(all(row.assigned_to_id for row in rows))
        self.assertTrue(all(row.status == TaskStatus.ASSIGNED for row in rows))
        loads = sorted(
            self.project.tasks.filter(assigned_to=user).count()
            for user in self.annotators
        )
        self.assertEqual(loads, [1, 2])

    def test_capacity_uses_canonical_assigned_tasks(self):
        first = self.tasks(3)[0]
        first.assigned_to = self.annotators[0]
        first.status = TaskStatus.ASSIGNED
        first.assigned_at = timezone.now()
        first.save(update_fields=["assigned_to", "status", "assigned_at"])
        AnnotatorProfile.objects.filter(user=self.annotators[1]).update(
            max_active_tasks=0
        )
        result = run_auto_fill(project=self.project)
        self.assertEqual(result.assignments_made, 1)
        self.assertEqual(
            self.project.tasks.filter(assigned_to=self.annotators[0]).count(), 2
        )

    def test_dry_run_records_but_does_not_assign(self):
        self.tasks(2)
        result = run_auto_fill(project=self.project, dry_run=True)
        self.assertEqual(len(result.proposals), 2)
        self.assertEqual(result.assignments_made, 0)
        self.assertFalse(self.project.tasks.exclude(assigned_to=None).exists())
        self.assertEqual(
            SchedulerDecision.objects.get(pk=result.decision_id).mode,
            SchedulerDecision.Mode.DRY_RUN,
        )

    def test_tick_key_is_idempotent(self):
        self.tasks(2)
        first = run_auto_fill(project=self.project, tick_key="one-tick")
        replay = run_auto_fill(project=self.project, tick_key="one-tick")
        self.assertEqual(first.assignments_made, 2)
        self.assertTrue(replay.replayed)
        self.assertEqual(SchedulerDecision.objects.count(), 1)

    def test_paused_project_is_not_assigned(self):
        self.tasks(1)
        self.project.paused = True
        self.project.save(update_fields=["paused"])
        self.assertEqual(run_auto_fill(project=self.project).assignments_made, 0)

    @override_settings(FEATURE_TEAMS=True)
    def test_team_grant_is_the_only_eligibility_policy(self):
        self.tasks(1)
        revoke_project_team(self.project, self.assignment_team)
        organization = Institution.objects.create(name="Scheduler org")
        team = Team.objects.create(organization=organization, name="Eligible")
        add_team_member(team, self.annotators[1])
        grant_project_team(self.project, team)
        result = run_auto_fill(project=self.project)
        self.assertEqual(result.assignments_made, 1)
        self.assertEqual(
            self.project.tasks.get().assigned_to_id, self.annotators[1].id
        )
