from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import AuditEvent


class MockLoginTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("mock-one", password="unused-strong-password")

    # Pinned off explicitly rather than left to the settings default. The
    # deployed `production_integrated_v1` profile ships ENABLE_MOCK_DEV_LOGIN
    # *on* — docs/product-invariants.md requires the Development accounts
    # section — so relying on the default made this assert the disabled
    # behaviour of an enabled endpoint and fail under the live profile. The
    # invariant under test is "off means 404", which is what this now says.
    @override_settings(ENABLE_MOCK_DEV_LOGIN=False)
    def test_disabled_is_not_exposed(self):
        self.assertEqual(self.client.get("/api/auth/mock-login/").status_code, 404)
        self.assertEqual(self.client.post("/api/auth/mock-login/", {"username": "mock-one"}).status_code, 404)

    @override_settings(
        ENABLE_MOCK_DEV_LOGIN=True,
        MOCK_DEV_LOGIN_ACCOUNTS=("mock-one",),
        MOCK_DEV_LOGIN_PASSWORD="demo-test-password",
    )
    def test_get_exposes_only_configured_click_to_fill_accounts(self):
        response = self.client.get("/api/auth/mock-login/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["accounts"][0]["username"], "mock-one")
        self.assertEqual(
            response.json()["accounts"][0]["password"], "demo-test-password"
        )
        direct = self.client.post(
            "/api/auth/mock-login/", {"username": "mock-one"}, format="json"
        )
        self.assertEqual(direct.status_code, 405)
        self.assertFalse(AuditEvent.objects.filter(verb="auth.mock_login").exists())

    @override_settings(
        ENABLE_MOCK_DEV_LOGIN=True,
        MOCK_DEV_LOGIN_ACCOUNTS=("manager", "mock-one"),
        MOCK_DEV_LOGIN_PASSWORD="demo-test-password",
    )
    def test_accounts_follow_configured_display_order(self):
        get_user_model().objects.create_user("manager", password=None)
        response = self.client.get("/api/auth/mock-login/")
        self.assertEqual(
            [item["username"] for item in response.json()["accounts"]],
            ["manager", "mock-one"],
        )
