"""Query-count guards for the task list endpoints.

``AnnotationTaskSerializer.review_history`` walks
``submissions -> reviews -> reviewer`` for every task it renders. That is a
serializer method field, so it runs per object: without a prefetch on the
queryset it costs a query per task (plus one per review row) and a project's
task list degrades linearly with its own history.

These tests pin the shape rather than an exact number: the query count must not
grow when more tasks (or more review history) are added to the same page.
"""

import tempfile

from django.contrib.auth.models import User
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import AnnotatorProfile, UserProfile
from annotation.models import AnnotationSubmission, AnnotationTask, ReviewRecord
from core.choices import TaskStatus, UserRole
from projects.models import Dataset, Project
from volumes.models import Volume

_TMP_ROOT = tempfile.mkdtemp(prefix="mito_query_test_")


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class TaskListQueryCountTests(APITestCase):
    def setUp(self):
        self.manager = User.objects.create_superuser("mgr", password="x")
        self.annotator = User.objects.create_user("ann", password="x")
        UserProfile.objects.update_or_create(
            user=self.annotator, defaults={"role": UserRole.ANNOTATOR}
        )
        AnnotatorProfile.objects.create(user=self.annotator)
        self.project = Project.objects.create(
            title="Query counts", created_by=self.manager
        )
        self.dataset = Dataset.objects.create(project=self.project, name="ds")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="vol"
        )

    def _make_task_with_history(self, rounds: int = 2) -> AnnotationTask:
        task = AnnotationTask.objects.create(
            project=self.project,
            volume=self.volume,
            assigned_to=self.annotator,
            status=TaskStatus.ASSIGNED,
            z_start=0,
            z_end=4,
            y_end=8,
            x_end=8,
        )
        for _ in range(rounds):
            submission = AnnotationSubmission.objects.create(
                task=task, annotator=self.annotator
            )
            ReviewRecord.objects.create(
                submission=submission, task=task, reviewer=self.manager
            )
        return task

    def _count_project_tasks_queries(self, task_count: int, rounds: int = 2) -> int:
        Project.objects.filter(pk=self.project.pk).update(title="reset")
        AnnotationTask.objects.filter(project=self.project).delete()
        for _ in range(task_count):
            self._make_task_with_history(rounds)

        self.client.force_authenticate(user=self.manager)
        url = reverse("api-project-tasks", args=[self.project.id])
        with CaptureQueriesContext(connection) as ctx:
            res = self.client.get(url)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(res.data), task_count)
        return len(ctx.captured_queries)

    def test_project_task_list_query_count_is_flat_in_task_count(self):
        few = self._count_project_tasks_queries(task_count=2)
        many = self._count_project_tasks_queries(task_count=8)
        self.assertEqual(
            few,
            many,
            "task list query count grew with the number of tasks - the "
            "review_history prefetch chain is not being used",
        )

    def test_project_task_list_query_count_is_flat_in_history_depth(self):
        shallow = self._count_project_tasks_queries(task_count=4, rounds=1)
        deep = self._count_project_tasks_queries(task_count=4, rounds=4)
        self.assertEqual(
            shallow,
            deep,
            "task list query count grew with review history depth - reviews "
            "or reviewers are being fetched per row",
        )

    def test_my_tasks_query_count_is_flat_in_task_count(self):
        def count(task_count: int) -> int:
            AnnotationTask.objects.filter(project=self.project).delete()
            for _ in range(task_count):
                self._make_task_with_history()
            self.client.force_authenticate(user=self.annotator)
            with CaptureQueriesContext(connection) as ctx:
                res = self.client.get(reverse("api-my-tasks"))
            self.assertEqual(res.status_code, 200, res.content)
            return len(ctx.captured_queries)

        self.assertEqual(
            count(2),
            count(8),
            "my-tasks query count grew with the number of tasks",
        )
