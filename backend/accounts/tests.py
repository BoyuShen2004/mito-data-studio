from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import AnnotatorProfile, UserProfile
from core.choices import UserRole


class RegistrationTests(APITestCase):
    def test_register_annotator_creates_profile(self):
        res = self.client.post(
            reverse("api-register"),
            {
                "username": "newann",
                "password": "s3cret-pass-99",
                "role": "annotator",
            },
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIn("token", res.data)
        self.assertEqual(res.data["user"]["role"], "annotator")
        user = User.objects.get(username="newann")
        self.assertEqual(user.profile.role, UserRole.ANNOTATOR)
        self.assertTrue(AnnotatorProfile.objects.filter(user=user).exists())

    def test_register_requester(self):
        res = self.client.post(
            reverse("api-register"),
            {
                "username": "newreq",
                "password": "s3cret-pass-99",
                "role": "requester",
                "institution_name": "Some Lab",
            },
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["user"]["role"], "requester")
        self.assertFalse(
            AnnotatorProfile.objects.filter(user__username="newreq").exists()
        )

    def test_public_manager_registration_is_rejected(self):
        res = self.client.post(
            reverse("api-register"),
            {"username": "wannabe", "password": "s3cret-pass-99", "role": "manager"},
        )
        self.assertEqual(res.status_code, 400)
        self.assertFalse(User.objects.filter(username="wannabe").exists())

    def test_duplicate_username_rejected(self):
        User.objects.create_user("taken", password="x")
        res = self.client.post(
            reverse("api-register"),
            {"username": "taken", "password": "s3cret-pass-99", "role": "annotator"},
        )
        self.assertEqual(res.status_code, 400)


class LoginPortalTests(APITestCase):
    def setUp(self):
        self.req = User.objects.create_user("req", password="pw12345678")
        UserProfile.objects.update_or_create(
            user=self.req, defaults={"role": UserRole.REQUESTER}
        )
        self.ann = User.objects.create_user("ann", password="pw12345678")
        UserProfile.objects.update_or_create(
            user=self.ann, defaults={"role": UserRole.ANNOTATOR}
        )

    def _login(self, username, portal):
        return self.client.post(
            reverse("api-login"),
            {"username": username, "password": "pw12345678", "portal": portal},
        )

    def test_requester_blocked_from_annotator_tab(self):
        self.assertEqual(self._login("req", "annotator").status_code, 403)

    def test_annotator_blocked_from_requester_tab(self):
        self.assertEqual(self._login("ann", "requester").status_code, 403)

    def test_correct_portal_succeeds(self):
        self.assertEqual(self._login("req", "requester").status_code, 200)
        self.assertEqual(self._login("ann", "annotator").status_code, 200)
