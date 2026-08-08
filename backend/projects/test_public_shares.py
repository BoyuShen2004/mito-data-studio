from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import Institution, UserProfile
from annotation.models import AnnotationTask
from core.choices import TaskStatus, UserRole
from projects.models import Dataset, Project, ProjectMembership, PublicShare
from volumes.models import Volume


class PublicShareApiTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("share-manager", password="pw")
        UserProfile.objects.update_or_create(user=self.manager, defaults={"role": UserRole.MANAGER})
        self.manager.refresh_from_db()
        self.org = Institution.objects.create(name="Share Org")
        self.project = Project.objects.create(title="Shared project", institution=self.org)
        self.ds1 = Dataset.objects.create(project=self.project, name="one")
        self.ds2 = Dataset.objects.create(project=self.project, name="two")
        self.v1 = Volume.objects.create(project=self.project, dataset=self.ds1, name="v1", image_path="v1.tif", shape_z=3, shape_y=4, shape_x=5)
        self.v2 = Volume.objects.create(project=self.project, dataset=self.ds2, name="v2", image_path="v2.tif", shape_z=3, shape_y=4, shape_x=5)
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def test_manager_creates_all_scopes_and_anonymous_browse_is_nested(self):
        for body, expected in (
            ({"scope": "project", "project_id": self.project.id}, {self.v1.id, self.v2.id}),
            ({"scope": "dataset", "project_id": self.project.id, "dataset_id": self.ds1.id}, {self.v1.id}),
            ({"scope": "volume", "project_id": self.project.id, "volume_id": self.v2.id}, {self.v2.id}),
        ):
            made = self.client.post(reverse("api-public-shares"), body, format="json")
            self.assertEqual(made.status_code, 201)
            anon = APIClient().get(reverse("api-public-share-browse", args=[made.json()["token"]]))
            self.assertEqual(anon.status_code, 200)
            self.assertEqual({v["id"] for v in anon.json()["volumes"]}, expected)

    def test_revoke_is_database_backed_and_old_link_explains_closure(self):
        row = PublicShare.objects.create(scope="project", project=self.project, created_by=self.manager)
        self.client.post(reverse("api-public-share-revoke", args=[row.id]))
        response = APIClient().get(reverse("api-public-share-browse", args=[row.token]))
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["detail"], "The manager closed this share.")

    def test_volume_share_reuses_live_token_and_revoke_closes_it(self):
        body = {
            "scope": "volume",
            "project_id": self.project.id,
            "volume_id": self.v1.id,
        }
        first = self.client.post(reverse("api-public-shares"), body, format="json")
        second = self.client.post(reverse("api-public-shares"), body, format="json")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(second.json()["token"], first.json()["token"])
        self.assertEqual(PublicShare.objects.filter(scope="volume", volume=self.v1).count(), 1)

        self.assertEqual(
            self.client.post(reverse("api-public-share-revoke", args=[first.json()["id"]])).status_code,
            200,
        )
        closed = APIClient().get(reverse("api-public-share-browse", args=[first.json()["token"]]))
        self.assertEqual(closed.status_code, 410)

    def test_non_manager_cannot_create_parent_scope_or_revoke_manager_share(self):
        user = User.objects.create_user("not-manager")
        UserProfile.objects.update_or_create(user=user, defaults={"role": UserRole.ANNOTATOR})
        row = PublicShare.objects.create(scope="volume", project=self.project, dataset=self.ds1, volume=self.v1, created_by=self.manager)
        self_owned_parent = PublicShare.objects.create(scope="project", project=self.project, created_by=user)
        self.client.force_authenticate(user)
        self.assertEqual(self.client.post(reverse("api-public-shares"), {"scope": "project", "project_id": self.project.id}).status_code, 403)
        self.assertEqual(self.client.post(reverse("api-public-share-revoke", args=[row.id])).status_code, 403)
        self.assertEqual(self.client.post(reverse("api-public-share-revoke", args=[self_owned_parent.id])).status_code, 403)
        self.assertIsNone(PublicShare.objects.get(pk=row.id).revoked_at)

    def test_annotator_can_share_accessible_volume_but_not_parent_scopes(self):
        user = User.objects.create_user("volume-sharer")
        UserProfile.objects.update_or_create(user=user, defaults={"role": UserRole.ANNOTATOR})
        user.refresh_from_db()
        ProjectMembership.objects.create(project=self.project, user=user, added_by=self.manager)
        self.client.force_authenticate(user)
        made = self.client.post(reverse("api-public-shares"), {
            "scope": "volume", "project_id": self.project.id, "volume_id": self.v1.id,
        }, format="json")
        self.assertEqual(made.status_code, 201)
        self.assertEqual(made.json()["created_by_username"], "volume-sharer")
        # The annotator may close the volume link they personally opened.
        self.assertEqual(
            self.client.post(reverse("api-public-share-revoke", args=[made.json()["id"]])).status_code,
            200,
        )
        self.assertEqual(self.client.post(reverse("api-public-shares"), {
            "scope": "dataset", "project_id": self.project.id, "dataset_id": self.ds1.id,
        }, format="json").status_code, 403)

        opened_for_manager = self.client.post(reverse("api-public-shares"), {
            "scope": "volume", "project_id": self.project.id, "volume_id": self.v1.id,
        }, format="json")
        self.assertEqual(opened_for_manager.status_code, 201)
        self.assertNotEqual(opened_for_manager.json()["id"], made.json()["id"])

        # A manager sees and may stop an annotator-created volume share.
        self.client.force_authenticate(self.manager)
        tree = self.client.get(reverse("api-public-share-tree")).json()["projects"][0]
        self.assertEqual(tree["state"], "partial")
        volume = tree["datasets"][0]["volumes"][0]
        self.assertTrue(volume["shared"])
        self.assertEqual(volume["direct_shares"][0]["created_by_username"], "volume-sharer")
        self.assertEqual(self.client.post(reverse("api-public-share-revoke", args=[opened_for_manager.json()["id"]])).status_code, 200)

        # Once a manager closes it, the annotator may immediately open a new
        # live share and regains ownership/Stop for that replacement.
        self.client.force_authenticate(user)
        reopened = self.client.post(reverse("api-public-shares"), {
            "scope": "volume", "project_id": self.project.id, "volume_id": self.v1.id,
        }, format="json")
        self.assertEqual(reopened.status_code, 201)
        self.assertNotEqual(reopened.json()["id"], opened_for_manager.json()["id"])
        self.assertEqual(APIClient().get(reverse("api-public-share-browse", args=[reopened.json()["token"]])).status_code, 200)
        self.assertEqual(
            self.client.post(reverse("api-public-share-revoke", args=[reopened.json()["id"]])).status_code,
            200,
        )

    def test_hierarchical_led_states_and_parent_stop_is_direct_only(self):
        extra = Volume.objects.create(project=self.project, dataset=self.ds1, name="v1b", image_path="v1b.tif")
        self.client.post(reverse("api-public-shares"), {
            "scope": "volume", "project_id": self.project.id, "volume_id": self.v1.id,
        }, format="json")
        tree = self.client.get(reverse("api-public-share-tree")).json()
        self.assertEqual(tree["stop_policy"], "direct_scope_only")
        project = tree["projects"][0]
        self.assertEqual(project["state"], "partial")
        ds1 = next(row for row in project["datasets"] if row["id"] == self.ds1.id)
        self.assertEqual(ds1["state"], "partial")

        dataset_share = self.client.post(reverse("api-public-shares"), {
            "scope": "dataset", "project_id": self.project.id, "dataset_id": self.ds1.id,
        }, format="json").json()
        project_share = self.client.post(reverse("api-public-shares"), {
            "scope": "project", "project_id": self.project.id,
        }, format="json").json()
        project = self.client.get(reverse("api-public-share-tree")).json()["projects"][0]
        self.assertEqual(project["state"], "all")
        self.assertEqual(next(row for row in project["datasets"] if row["id"] == self.ds1.id)["state"], "all")

        # Stopping the project link leaves dataset/volume tokens untouched.
        self.client.post(reverse("api-public-share-revoke", args=[project_share["id"]]))
        project = self.client.get(reverse("api-public-share-tree")).json()["projects"][0]
        self.assertEqual(project["state"], "partial")
        self.assertTrue(PublicShare.objects.get(pk=dataset_share["id"]).revoked_at is None)
        self.assertTrue(PublicShare.objects.filter(scope="volume", volume=self.v1, revoked_at__isnull=True).exists())

        # Avoid an unused fixture silently escaping the dataset aggregate.
        self.assertIn(extra.id, [row["id"] for row in next(d for d in project["datasets"] if d["id"] == self.ds1.id)["volumes"]])
