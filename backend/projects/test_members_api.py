from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from accounts.models import Institution, Team, TeamMembership, UserProfile
from accounts.teams import add_team_member, is_eligible_project_assignee
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

    def test_adding_access_also_makes_the_annotator_assignable(self):
        newcomer = User.objects.create_user("new-access-annotator", password="x")
        UserProfile.objects.filter(user=newcomer).update(role=UserRole.ANNOTATOR)
        response = self.client.post(
            f"/api/projects/{self.project.id}/members/",
            {"user_id": newcomer.id},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.project.refresh_from_db()
        self.assertTrue(is_eligible_project_assignee(newcomer, self.project))
        row = next(
            item
            for item in self.client.get(
                f"/api/projects/{self.project.id}/members/"
            ).data
            if item["user_id"] == newcomer.id
        )
        self.assertTrue(row["is_explicit"])
        self.assertTrue(row["is_working_team"])

    def test_adding_to_the_working_team_also_creates_access(self):
        newcomer = User.objects.create_user("new-team-annotator", password="x")
        UserProfile.objects.filter(user=newcomer).update(role=UserRole.ANNOTATOR)
        add_team_member(self.project.working_team, newcomer, actor=self.manager)
        self.assertTrue(
            self.project.memberships.filter(user=newcomer).exists()
        )

    def test_access_add_creates_a_default_working_team_when_missing(self):
        project = create_project(title="No team yet", reviewed=True)
        newcomer = User.objects.create_user("default-team-annotator", password="x")
        UserProfile.objects.filter(user=newcomer).update(role=UserRole.ANNOTATOR)
        response = self.client.post(
            f"/api/projects/{project.id}/members/",
            {"user_id": newcomer.id},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        project.refresh_from_db()
        self.assertIsNotNone(project.working_team_id)
        self.assertTrue(is_eligible_project_assignee(newcomer, project))
