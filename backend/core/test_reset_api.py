from django.contrib.auth import get_user_model
import tempfile
from pathlib import Path

from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Institution, Team, TeamMembership
from projects.models import Dataset, Project
from volumes.models import Volume


class ResetAuthorizationTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.normal = User.objects.create_user("normal", password="strong-test-password")
        self.admin = User.objects.create_superuser("admin-api", password="strong-test-password")

    def test_normal_user_cannot_read_or_call_reset(self):
        self.client.force_authenticate(self.normal)
        for path in ("/api/admin/reset/status/", "/api/admin/reset/confirm/", "/api/admin/reset/execute/"):
            response = self.client.get(path) if path.endswith("status/") else self.client.post(path, {}, format="json")
            self.assertEqual(response.status_code, 403)

    @override_settings(MITO_RESET_BACKUP_MARKER="")
    def test_superuser_status_is_read_only_and_reports_disabled_gates(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/admin/reset/status/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["maintenance"])
        self.assertFalse(response.json()["backup"]["valid"])
        self.assertTrue({
            "teams", "memberships", "project_team_grants", "public_shares",
            "hard_cases", "submissions",
            "annotation_operations", "processing_jobs",
        }.issubset(response.json()["clear"]))

    def test_destructive_posts_require_csrf_even_with_api_token(self):
        token = Token.objects.create(user=self.admin)
        client = APIClient(enforce_csrf_checks=True)
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        denied = client.post("/api/admin/reset/confirm/", {
            "password": "strong-test-password", "phrase": "CLEAR ALL APPLICATION DATA",
        }, format="json")
        self.assertEqual(denied.status_code, 403)
        status_response = client.get("/api/admin/reset/status/")
        csrf = status_response.cookies["csrftoken"].value
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}", HTTP_X_CSRFTOKEN=csrf)
        gated = client.post("/api/admin/reset/confirm/", {
            "password": "strong-test-password", "phrase": "CLEAR ALL APPLICATION DATA",
        }, format="json")
        self.assertEqual(gated.status_code, 409)


class DevelopmentResetApiTests(APITestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            "admin", password="strong-test-password"
        )
        self.mock = User.objects.create_user(
            "mock-one", password="strong-test-password"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_endpoint_is_absent_unless_both_dev_switches_are_enabled(self):
        with override_settings(
            ENABLE_MOCK_DEV_LOGIN=True,
            MITO_ALLOW_DEV_RESET=False,
            MOCK_DEV_LOGIN_ACCOUNTS=("mock-one",),
        ):
            self.assertEqual(
                self.client.get("/api/auth/development-reset/").status_code,
                404,
            )

    @override_settings(
        ENABLE_MOCK_DEV_LOGIN=True,
        MITO_ALLOW_DEV_RESET=True,
        MOCK_DEV_LOGIN_ACCOUNTS=("mock-one",),
        MITO_RESET_ADMIN_USERNAME="admin",
    )
    def test_csrf_and_confirmation_protect_complete_passwordless_reset(self):
        organization = Institution.objects.create(name="Disposable")
        team = Team.objects.create(organization=organization, name="Team")
        TeamMembership.objects.create(team=team, user=self.mock)
        project = Project.objects.create(title="Project", created_by=self.mock)
        project.teams.add(team)
        dataset = Dataset.objects.create(project=project, name="Dataset")
        Volume.objects.create(project=project, dataset=dataset, name="Volume")
        (self.root / "owned.bin").write_bytes(b"owned")

        client = APIClient(enforce_csrf_checks=True)
        with override_settings(MITO_DATA_ROOT=self.root):
            denied = client.post(
                "/api/auth/development-reset/",
                {"confirmation": "CLEAR ALL DEVELOPMENT DATA"},
                format="json",
            )
            self.assertEqual(denied.status_code, 403)

            status_response = client.get("/api/auth/development-reset/")
            self.assertEqual(status_response.status_code, 200)
            self.assertEqual(status_response.json()["clear"]["teams"], 1)
            csrf = status_response.cookies["csrftoken"].value

            wrong = client.post(
                "/api/auth/development-reset/",
                {"confirmation": "yes"},
                format="json",
                HTTP_X_CSRFTOKEN=csrf,
            )
            self.assertEqual(wrong.status_code, 409)

            response = client.post(
                "/api/auth/development-reset/",
                {"confirmation": "CLEAR ALL DEVELOPMENT DATA"},
                format="json",
                HTTP_X_CSRFTOKEN=csrf,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["after"]["projects"], 0)
        self.assertEqual(response.json()["after"]["teams"], 0)
        self.assertEqual(response.json()["after"]["memberships"], 0)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(
            set(get_user_model().objects.values_list("username", flat=True)),
            {"admin", "mock-one"},
        )
