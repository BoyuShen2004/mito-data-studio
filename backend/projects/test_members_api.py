from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from accounts.models import Institution, Team, TeamMembership, UserProfile
from core.choices import UserRole
from projects.services import create_project


class ProjectMembersApiTests(APITestCase):
    def setUp(self):
        self.manager = User.objects.create_superuser("access-manager", password="x")
        self.annotator = User.objects.create_user("howie", password="x")
        UserProfile.objects.update_or_create(
            user=self.annotator,
            defaults={"role": UserRole.ANNOTATOR, "display_name": "Howie L."},
        )
        institution = Institution.objects.create(name="Access Lab")
        team = Team.objects.create(organization=institution, name="nag_p10")
        TeamMembership.objects.create(team=team, user=self.annotator)
        self.project = create_project(title="Access project", reviewed=True)
        self.project.teams.add(team)
        self.project.working_team = team
        self.project.save(update_fields=["working_team"])
        self.client.force_authenticate(self.manager)

    def test_working_team_member_without_tasks_is_listed(self):
        response = self.client.get(f"/api/projects/{self.project.id}/members/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 1)
        row = response.data[0]
        self.assertEqual(row["user_id"], self.annotator.id)
        self.assertEqual(row["display_name"], "Howie L.")
        self.assertFalse(row["is_explicit"])
        self.assertTrue(row["is_working_team"])
        self.assertFalse(row["has_tasks"])
        self.assertEqual(row["access_reason"], "Working team")
        self.assertIsNone(row["membership_id"])
