"""API coverage for the simplified manager team surface."""

import tempfile

import numpy as np
import tifffile

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import AnnotatorProfile, Institution, Team, TeamMembership, UserProfile
from annotation.label_paths import working_label_rel_path
from annotation.models import AnnotationTask, AssignmentWithdrawal
from annotation.visualization.slice_io import resolve_path
from core.choices import TaskStatus, TaskType, UserRole
from projects.models import Project
from volumes.services import register_volume


_TMP_ROOT = tempfile.mkdtemp(prefix="mito_team_delete_")


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class CollaborationApiTests(APITestCase):
    def setUp(self):
        self.organization = Institution.objects.create(name="Project Lab")
        self.manager = User.objects.create_superuser("team-manager", password="x")
        UserProfile.objects.update_or_create(
            user=self.manager,
            defaults={"role": UserRole.MANAGER, "institution": self.organization},
        )
        self.annotator = User.objects.create_user("team-annotator", password="x")
        UserProfile.objects.update_or_create(
            user=self.annotator, defaults={"role": UserRole.ANNOTATOR}
        )
        AnnotatorProfile.objects.create(user=self.annotator)
        self.requester = User.objects.create_user("team-requester", password="x")
        UserProfile.objects.update_or_create(
            user=self.requester, defaults={"role": UserRole.REQUESTER}
        )
        self.project = Project.objects.create(
            title="Mito Project", institution=self.organization, created_by=self.manager
        )
        self.client.force_authenticate(self.manager)

    def test_create_infers_project_organization_members_and_grant(self):
        response = self.client.post(
            reverse("api-collaboration"),
            {
                "action": "create_team",
                "project_id": self.project.id,
                "member_ids": [self.annotator.id],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        team = Team.objects.get(name=self.project.title)
        self.assertEqual(team.organization, self.organization)
        self.assertTrue(TeamMembership.objects.filter(team=team, user=self.annotator).exists())
        self.assertTrue(self.project.teams.filter(pk=team.pk).exists())
        self.project.refresh_from_db()
        self.assertEqual(self.project.working_team_id, team.id)
        self.assertEqual(response.data["mutation"]["team_id"], team.id)

    def test_team_can_be_renamed_and_only_annotators_can_be_added(self):
        team = Team.objects.create(organization=self.organization, name="Old")
        renamed = self.client.post(
            reverse("api-collaboration"),
            {"action": "rename_team", "team_id": team.id, "name": "New"},
            format="json",
        )
        self.assertEqual(renamed.status_code, 200, renamed.data)
        team.refresh_from_db()
        self.assertEqual(team.name, "New")

        rejected = self.client.post(
            reverse("api-collaboration"),
            {"action": "add_team_member", "team_id": team.id, "user_id": self.requester.id},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertFalse(TeamMembership.objects.filter(team=team, user=self.requester).exists())

    def test_people_hub_create_uses_manager_organization_without_form_field(self):
        response = self.client.post(
            reverse("api-collaboration"),
            {"action": "create_team", "name": "Hub team", "member_ids": []},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Team.objects.get(name="Hub team").organization, self.organization)

    def test_delete_unused_team_removes_it(self):
        team = Team.objects.create(organization=self.organization, name="Unused")
        response = self.client.post(
            reverse("api-collaboration"),
            {"action": "delete_team", "team_id": team.id, "confirm": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(Team.objects.filter(pk=team.id).exists())
        self.assertEqual(response.data["mutation"]["task_count"], 0)

    def test_delete_working_team_promotes_mask_and_records_cancelled_done_item(self):
        team = Team.objects.create(organization=self.organization, name="Working")
        TeamMembership.objects.create(team=team, user=self.annotator)
        self.project.teams.add(team)
        self.project.working_team = team
        self.project.save(update_fields=["working_team"])
        volume = register_volume(
            project=self.project,
            name="assigned-volume",
            image_path="assigned-volume.tif",
            autodetect_shape=False,
        )
        volume.shape_z, volume.shape_y, volume.shape_x = 2, 3, 4
        volume.save(update_fields=["shape_z", "shape_y", "shape_x"])
        task = AnnotationTask.objects.create(
            project=self.project,
            volume=volume,
            assigned_to=self.annotator,
            status=TaskStatus.IN_PROGRESS,
            task_type=TaskType.MANUAL_ANNOTATION,
            z_start=0,
            z_end=2,
            y_end=3,
            x_end=4,
        )
        working = resolve_path(working_label_rel_path(volume))
        working.parent.mkdir(parents=True, exist_ok=True)
        mask = np.full((2, 3, 4), 7, dtype=np.uint16)
        tifffile.imwrite(working, mask)

        response = self.client.post(
            reverse("api-collaboration"),
            {"action": "delete_team", "team_id": team.id, "confirm": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        task.refresh_from_db()
        volume.refresh_from_db()
        self.project.refresh_from_db()
        self.assertIsNone(task.assigned_to_id)
        self.assertEqual(task.status, TaskStatus.UNASSIGNED)
        self.assertIsNone(self.project.working_team_id)
        self.assertEqual(volume.label_path, working_label_rel_path(volume))
        np.testing.assert_array_equal(tifffile.imread(working), mask)
        withdrawal = AssignmentWithdrawal.objects.get(task=task)
        self.assertEqual(withdrawal.annotator, self.annotator)

        self.client.force_authenticate(self.annotator)
        done = self.client.get(reverse("api-my-completed-tasks"))
        self.assertEqual(done.status_code, 200, done.data)
        cancelled = next(row for row in done.data if row["status"] == "cancelled")
        self.assertTrue(cancelled["assignment_withdrawn"])
        self.assertEqual(cancelled["withdrawal_team"], "Working")
