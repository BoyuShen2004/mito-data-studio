"""Tests for the New / To Proofread / Done lifecycle mapping."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from annotation.models import AnnotationTask
from core.choices import ProjectStatus, TaskStatus, TaskType, WorkflowType
from core.lifecycle import (
    Lifecycle,
    classify_project,
    lifecycle_for_task_status,
    project_lifecycle_counts,
)
from projects.services import create_project, resolve_workflow_type
from volumes.models import Volume

User = get_user_model()


class TaskLifecycleTests(TestCase):
    def test_every_task_status_maps_to_a_bucket(self):
        for status in TaskStatus.values:
            bucket = lifecycle_for_task_status(status)
            self.assertIn(bucket, Lifecycle.values)

    def test_approved_is_done_active_is_to_proofread(self):
        self.assertEqual(
            lifecycle_for_task_status(TaskStatus.APPROVED), Lifecycle.DONE
        )
        for status in (
            TaskStatus.UNASSIGNED,
            TaskStatus.ASSIGNED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.SUBMITTED,
            TaskStatus.REVISION_REQUESTED,
            TaskStatus.REJECTED,
        ):
            self.assertEqual(
                lifecycle_for_task_status(status), Lifecycle.TO_PROOFREAD
            )


class ProjectLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("mgr", password="x")

    def _project(self, reviewed=False, status=None):
        return create_project(
            title="P", created_by=self.user, reviewed=reviewed, status=status
        )

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

    def test_unreviewed_project_is_new(self):
        self.assertEqual(classify_project(self._project()), Lifecycle.NEW)

    def test_reviewed_without_tasks_is_new(self):
        project = self._project(reviewed=True)
        self.assertEqual(classify_project(project), Lifecycle.NEW)

    def test_reviewed_with_active_task_is_to_proofread(self):
        project = self._project(reviewed=True)
        self._task(project, TaskStatus.ASSIGNED)
        self.assertEqual(classify_project(project), Lifecycle.TO_PROOFREAD)

    def test_all_tasks_approved_is_done(self):
        project = self._project(reviewed=True)
        self._task(project, TaskStatus.APPROVED)
        self.assertEqual(classify_project(project), Lifecycle.DONE)

    def test_mixed_tasks_is_to_proofread(self):
        project = self._project(reviewed=True)
        self._task(project, TaskStatus.APPROVED)
        self._task(project, TaskStatus.IN_PROGRESS)
        self.assertEqual(classify_project(project), Lifecycle.TO_PROOFREAD)

    def test_terminal_status_forces_done(self):
        project = self._project(reviewed=True, status=ProjectStatus.DELIVERED)
        self._task(project, TaskStatus.IN_PROGRESS)
        self.assertEqual(classify_project(project), Lifecycle.DONE)

    def test_lifecycle_counts(self):
        self._project()  # new
        p2 = self._project(reviewed=True)
        self._task(p2, TaskStatus.ASSIGNED)  # to_proofread
        p3 = self._project(reviewed=True)
        self._task(p3, TaskStatus.APPROVED)  # done
        counts = project_lifecycle_counts([self._project(), p2, p3])
        self.assertEqual(counts[Lifecycle.TO_PROOFREAD], 1)
        self.assertEqual(counts[Lifecycle.DONE], 1)
        self.assertGreaterEqual(counts[Lifecycle.NEW], 1)


class WorkflowTypeTests(TestCase):
    def test_resolve_explicit_wins(self):
        self.assertEqual(
            resolve_workflow_type(WorkflowType.SEGMENTATION, "proofreading"),
            WorkflowType.SEGMENTATION,
        )

    def test_resolve_derives_from_annotation_type(self):
        self.assertEqual(
            resolve_workflow_type(None, "proofreading"), WorkflowType.PROOFREADING
        )
        self.assertEqual(
            resolve_workflow_type(None, "instance_segmentation"),
            WorkflowType.ANNOTATION,
        )

    def test_resolve_defaults_to_annotation(self):
        self.assertEqual(resolve_workflow_type(None, None), WorkflowType.ANNOTATION)

    def test_create_project_sets_workflow_type(self):
        user = User.objects.create_user("u", password="x")
        project = create_project(
            title="P", created_by=user, annotation_type="proofreading"
        )
        self.assertEqual(project.workflow_type, WorkflowType.PROOFREADING)
