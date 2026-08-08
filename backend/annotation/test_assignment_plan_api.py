"""Regression coverage for the bounded, manager-facing push assignment API."""

import tempfile

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import AnnotatorProfile, Institution, Team, TeamMembership, UserProfile
from core.choices import LabelType, UserRole
from projects.services import create_project
from volumes.services import register_volume


_TMP_ROOT = tempfile.mkdtemp(prefix="mito_assignment_plan_api_")


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class AssignmentPlanApiTests(APITestCase):
    def setUp(self):
        self.manager = User.objects.create_superuser("plan-manager", password="x")
        self.annotator = User.objects.create_user("plan-annotator", password="x")
        UserProfile.objects.update_or_create(
            user=self.annotator, defaults={"role": UserRole.ANNOTATOR}
        )
        AnnotatorProfile.objects.create(
            user=self.annotator, is_active_annotator=True, max_active_tasks=50
        )
        self.project = create_project(title="Large push plan", reviewed=True)
        self.organization = Institution.objects.create(name="Plan Lab")
        self.team = Team.objects.create(organization=self.organization, name="Plan Team")
        TeamMembership.objects.create(team=self.team, user=self.annotator)
        self.project.teams.add(self.team)
        self.project.working_team = self.team
        self.project.save(update_fields=["working_team"])
        for index in range(24):
            volume = register_volume(
                project=self.project,
                name=f"volume-{index:02d}",
                image_path=f"volume-{index:02d}.tiff",
                label_type=LabelType.NONE,
                autodetect_shape=False,
            )
            volume.shape_x = volume.shape_y = 16
            volume.shape_z = 8
            volume.voxel_size_z = 4.0
            volume.voxel_size_y = volume.voxel_size_x = 1.5
            if index == 0:
                volume.region_mask_path = "regions/volume-00.tiff"
                volume.region_mask_coverage = 0.25
            volume.save(update_fields=[
                "shape_x", "shape_y", "shape_z",
                "voxel_size_x", "voxel_size_y", "voxel_size_z",
                "region_mask_path", "region_mask_coverage",
            ])
        self.client.force_authenticate(self.manager)

    def test_rows_are_bounded_and_create_one_task_per_volume(self):
        response = self.client.post(
            reverse("api-assign-plan-rows", args=[self.project.id]), {}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["entries"]), 24)
        self.assertEqual(response.data["created_tasks"], 24)
        first = next(
            row for row in response.data["entries"]
            if row["volume_name"] == "volume-00"
        )
        self.assertIn("volume_name", first)
        self.assertEqual(first["file_format"], "tiff")
        self.assertEqual(
            [first["shape_z"], first["shape_y"], first["shape_x"]],
            [8, 16, 16],
        )
        self.assertEqual(
            [first["voxel_size_z"], first["voxel_size_y"], first["voxel_size_x"]],
            [4.0, 1.5, 1.5],
        )
        self.assertTrue(first["has_region_mask"])
        self.assertEqual(first["region_mask_coverage"], 0.25)
        self.assertNotIn("total_instances", first)
        self.assertNotIn("pending_instances", first)
        self.assertNotIn("review_history", first)
        self.assertNotIn("instances", first)
        self.assertNotIn("image_location", first)

        # A repeat load is idempotent and does not duplicate tasks.
        repeated = self.client.post(
            reverse("api-assign-plan-rows", args=[self.project.id]), {}, format="json"
        )
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(repeated.data["created_tasks"], 0)
        self.assertEqual(len(repeated.data["entries"]), 24)

    def test_preview_and_apply_keep_push_assignment_distinct(self):
        preview = self.client.post(
            reverse("api-assign-plan-preview", args=[self.project.id]), {}, format="json"
        )
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(len(preview.data["entries"]), 24)
        entry = preview.data["entries"][0]
        self.assertEqual(entry["proposed_annotator_id"], self.annotator.id)

        applied = self.client.post(
            reverse("api-assign-plan-apply", args=[self.project.id]),
            {"entries": [{
                "task_id": entry["id"],
                "annotator_id": self.annotator.id,
                "priority": 4,
                "difficulty": 2,
                "instructions": "Manager push assignment",
                "deadline": None,
            }]},
            format="json",
        )
        self.assertEqual(applied.status_code, 200, applied.data)
        self.assertEqual(applied.data["updated"], 1)
        self.assertEqual(applied.data["assigned"], 1)

    def test_apply_rejects_annotator_outside_eligible_teams(self):
        outsider = User.objects.create_user("plan-outsider", password="x")
        UserProfile.objects.update_or_create(
            user=outsider, defaults={"role": UserRole.ANNOTATOR}
        )
        AnnotatorProfile.objects.create(
            user=outsider, is_active_annotator=True, max_active_tasks=50
        )
        rows = self.client.post(
            reverse("api-assign-plan-rows", args=[self.project.id]), {}, format="json"
        )
        response = self.client.post(
            reverse("api-assign-plan-apply", args=[self.project.id]),
            {"entries": [{
                "task_id": rows.data["entries"][0]["id"],
                "annotator_id": outsider.id,
            }]},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("eligible for this project", response.data["detail"])

    def test_create_team_then_assign_through_single_task_endpoint(self):
        newcomer = User.objects.create_user("plan-newcomer", password="x")
        UserProfile.objects.update_or_create(
            user=newcomer, defaults={"role": UserRole.ANNOTATOR}
        )
        AnnotatorProfile.objects.create(
            user=newcomer, is_active_annotator=True, max_active_tasks=50
        )
        created = self.client.post(
            reverse("api-collaboration"),
            {
                "action": "create_team",
                "project_id": self.project.id,
                "name": self.project.title,
                "member_ids": [newcomer.id],
            },
            format="json",
        )
        self.assertEqual(created.status_code, 200, created.data)
        rows = self.client.post(
            reverse("api-assign-plan-rows", args=[self.project.id]), {}, format="json"
        )
        task_id = rows.data["entries"][0]["id"]
        assigned = self.client.post(
            reverse("api-task-assign", args=[task_id]),
            {"annotator_id": newcomer.id},
            format="json",
        )
        self.assertEqual(assigned.status_code, 200, assigned.data)
        self.assertEqual(assigned.data["assigned_to"], newcomer.id)
