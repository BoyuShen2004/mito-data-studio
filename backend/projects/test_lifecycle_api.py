"""API tests for lifecycle filtering, counts, and Institution data isolation."""

import tempfile

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import UserProfile
from annotation.models import AnnotationTask
from core.choices import TaskStatus, TaskType, UserRole
from projects.services import create_project
from volumes.models import Volume

_TMP_ROOT = tempfile.mkdtemp(prefix="mito_lifecycle_api_")


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class LifecycleApiTests(APITestCase):
    def setUp(self):
        self.manager = User.objects.create_superuser("mgr", password="x")
        inst_a = User.objects.create_user("inst_a", password="x")
        inst_b = User.objects.create_user("inst_b", password="x")
        for u in (inst_a, inst_b):
            UserProfile.objects.update_or_create(
                user=u, defaults={"role": UserRole.REQUESTER}
            )
        # Re-fetch so the cached (signal-created) annotator profile is dropped.
        self.inst_a = User.objects.get(pk=inst_a.pk)
        self.inst_b = User.objects.get(pk=inst_b.pk)

        # inst_a: one New project, one Done project.
        self.new_project = create_project(
            title="A-new", created_by=self.inst_a, reviewed=False
        )
        self.done_project = create_project(
            title="A-done", created_by=self.inst_a, reviewed=True
        )
        self._task(self.done_project, TaskStatus.APPROVED)

        # inst_b: one To Proofread project (should be invisible to inst_a).
        self.b_project = create_project(
            title="B-active", created_by=self.inst_b, reviewed=True
        )
        self._task(self.b_project, TaskStatus.ASSIGNED)

    def _task(self, project, status):
        volume = Volume.objects.create(project=project, name="v", shape_z=4)
        return AnnotationTask.objects.create(
            project=project,
            volume=volume,
            z_start=0,
            z_end=4,
            y_end=4,
            x_end=4,
            task_type=TaskType.MANUAL_ANNOTATION,
            status=status,
        )

    def test_institution_sees_only_own_projects(self):
        self.client.force_authenticate(user=self.inst_a)
        res = self.client.get(reverse("project-list"))
        titles = {p["title"] for p in res.data}
        self.assertEqual(titles, {"A-new", "A-done"})
        self.assertNotIn("B-active", titles)

    def test_lifecycle_filter(self):
        self.client.force_authenticate(user=self.inst_a)
        res = self.client.get(reverse("project-list"), {"lifecycle": "new"})
        self.assertEqual([p["title"] for p in res.data], ["A-new"])
        res = self.client.get(reverse("project-list"), {"lifecycle": "done"})
        self.assertEqual([p["title"] for p in res.data], ["A-done"])

    def test_lifecycle_serialized_on_project(self):
        self.client.force_authenticate(user=self.inst_a)
        res = self.client.get(reverse("project-list"))
        by_title = {p["title"]: p for p in res.data}
        self.assertEqual(by_title["A-new"]["lifecycle"], "new")
        self.assertEqual(by_title["A-done"]["lifecycle"], "done")
        self.assertIn("workflow_type", by_title["A-new"])

    def test_lifecycle_counts_endpoint_scoped_to_owner(self):
        self.client.force_authenticate(user=self.inst_a)
        res = self.client.get(reverse("project-lifecycle-counts"))
        self.assertEqual(res.data["new"], 1)
        self.assertEqual(res.data["done"], 1)
        self.assertEqual(res.data["to_proofread"], 0)  # B-active is inst_b's

    def test_manager_sees_all_lifecycle_counts(self):
        self.client.force_authenticate(user=self.manager)
        res = self.client.get(reverse("project-lifecycle-counts"))
        self.assertEqual(res.data["new"], 1)
        self.assertEqual(res.data["to_proofread"], 1)
        self.assertEqual(res.data["done"], 1)
