import io
import os
import tempfile

import numpy as np
import tifffile
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import AnnotatorProfile, Institution, Team, TeamMembership
from annotation.services import (
    apply_assignment_plan,
    create_whole_volume_task,
    assign_task_to_annotator,
    assign_tasks_rule_based,
    auto_assign_project,
    calculate_annotator_workload,
    ensure_volume_tasks,
    preview_assign_project,
    review_submission,
    submit_annotation,
)
from core.choices import (
    LabelType,
    QCStatus,
    ReviewDecision,
    TaskStatus,
    TaskType,
)
from projects.services import calculate_project_progress, create_project
from volumes.services import register_volume

from .models import AnnotationTask

_TMP_ROOT = tempfile.mkdtemp(prefix="mito_test_")


def make_annotator(username, max_active=5):
    user = User.objects.create_user(username=username, password="x")
    AnnotatorProfile.objects.create(
        user=user, is_active_annotator=True, max_active_tasks=max_active,
    )
    return user


def grant_assignment_team(project, users):
    """Make ``users`` eligible through the same explicit grant the UI creates."""
    organization = Institution.objects.create(name=f"Assignment org {project.pk}")
    team = Team.objects.create(
        organization=organization, name=f"{project.title} assignment team"
    )
    TeamMembership.objects.bulk_create(
        [TeamMembership(team=team, user=user) for user in users]
    )
    project.teams.add(team)
    project.working_team = team
    project.save(update_fields=["working_team"])
    return team


def make_volume_tasks(project, count, *, shape=(32, 64, 64), label_type=LabelType.NONE,
                      name_prefix="vol"):
    """Register ``count`` volumes and give each its one whole-volume task.

    A volume is one assignable unit, so N assignable tasks means N volumes —
    there is no way to conjure several tasks out of a single volume any more.
    """
    shape_z, shape_y, shape_x = shape
    tasks = []
    for index in range(count):
        # Real raw data on disk: approving a submission installs it as the
        # official label and re-seeds the working draft, and that reset reads
        # the registered image to size the new mask.
        tifffile.imwrite(
            os.path.join(_TMP_ROOT, f"{name_prefix}{index}.tiff"),
            np.zeros(shape, dtype=np.uint8),
        )
        volume = register_volume(
            project=project,
            name=f"{name_prefix}{index}",
            image_path=f"{name_prefix}{index}.tiff",
            label_path="" if label_type == LabelType.NONE else f"{name_prefix}{index}_mask.tiff",
            label_type=label_type,
            autodetect_shape=False,
        )
        volume.shape_z, volume.shape_y, volume.shape_x = shape_z, shape_y, shape_x
        volume.save()
        task = create_whole_volume_task(volume)
        assert task is not None
        tasks.append(task)
    return tasks


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class WorkflowIntegrationTests(TestCase):
    def setUp(self):
        self.project = create_project(title="Mito project")

    def test_full_workflow(self):
        # Two volumes, therefore two assignable whole-volume tasks.
        tasks = make_volume_tasks(self.project, 2)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(AnnotationTask.objects.count(), 2)
        self.assertTrue(
            all(t.status == TaskStatus.UNASSIGNED for t in tasks)
        )

        # Assign to an annotator.
        annotator = make_annotator("ann1")
        grant_assignment_team(self.project, [annotator])
        result = assign_tasks_rule_based(project=self.project)
        self.assertEqual(result["assigned"], 2)
        self.assertEqual(result["remaining_unassigned"], 0)
        task = AnnotationTask.objects.filter(assigned_to=annotator).first()
        self.assertEqual(task.status, TaskStatus.ASSIGNED)
        self.assertIsNotNone(task.assigned_at)

        # Annotator submits a label file; QC runs. The mask is a readable TIFF
        # of the registered shape because approve installs it as official.
        payload = io.BytesIO()
        tifffile.imwrite(payload, np.zeros((32, 64, 64), dtype=np.uint16))
        upload = SimpleUploadedFile(
            "vol1_z0.tif", payload.getvalue(), content_type="image/tiff"
        )
        submission = submit_annotation(
            task=task, annotator=annotator, label_file=upload, notes="done"
        )
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.SUBMITTED)
        self.assertEqual(submission.qc_status, QCStatus.PASSED)

        # Manager approves -> task approved (annotation work is unpaid).
        manager = User.objects.create_user("mgr", password="x", is_superuser=True)
        review_submission(
            submission=submission,
            reviewer=manager,
            decision=ReviewDecision.APPROVED,
            comments="ok",
        )
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.APPROVED)
        self.assertIsNotNone(task.approved_at)

        # Progress reflects one approved of two tasks.
        progress = calculate_project_progress(self.project)
        self.assertEqual(progress["total_tasks"], 2)
        self.assertEqual(progress["approved_tasks"], 1)
        self.assertEqual(progress["percent_complete"], 50.0)

    def test_reject_and_revision_paths(self):
        tasks = make_volume_tasks(self.project, 1)
        annotator = make_annotator("ann2")
        grant_assignment_team(self.project, [annotator])
        assign_tasks_rule_based(project=self.project)
        task = tasks[0]
        task.refresh_from_db()

        upload = SimpleUploadedFile("x.tif", b"data")
        submission = submit_annotation(task=task, annotator=annotator, label_file=upload)

        review_submission(
            submission=submission, reviewer=None,
            decision=ReviewDecision.REVISION_REQUESTED,
        )
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.REVISION_REQUESTED)

    def test_qc_rejects_bad_extension(self):
        tasks = make_volume_tasks(self.project, 1)
        annotator = make_annotator("ann3")
        grant_assignment_team(self.project, [annotator])
        assign_tasks_rule_based(project=self.project)
        task = tasks[0]
        # `assign_tasks_rule_based` updates rows, not the objects `tasks` holds,
        # so this one still reported UNASSIGNED and `submit_annotation` refused
        # the UNASSIGNED -> SUBMITTED transition. (Only visible where
        # `assert_transition` is enforcing, i.e. FEATURE_REVIEW_HISTORY on —
        # the default under the deployed profile.)
        task.refresh_from_db()
        bad = SimpleUploadedFile("notes.txt", b"not a label")
        submission = submit_annotation(task=task, annotator=annotator, label_file=bad)
        self.assertEqual(submission.qc_status, QCStatus.FAILED)


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class AssignmentCapacityTests(TestCase):
    def setUp(self):
        self.project = create_project(title="Capacity")

    def test_respects_max_active_tasks(self):
        # 4 volumes -> 4 tasks; one annotator capped at 2 -> only 2 assigned.
        make_volume_tasks(self.project, 4, shape=(64, 8, 8))
        annotator = make_annotator("cap", max_active=2)
        grant_assignment_team(self.project, [annotator])
        result = assign_tasks_rule_based(project=self.project)
        self.assertEqual(result["assigned"], 2)
        self.assertEqual(result["remaining_unassigned"], 2)

    def test_priority_ordering(self):
        tasks = make_volume_tasks(self.project, 4, shape=(64, 8, 8))
        # Bump one task's priority; it should be assigned first.
        hi = tasks[-1]
        hi.priority = 10
        hi.save()
        annotator = make_annotator("solo", max_active=1)
        grant_assignment_team(self.project, [annotator])
        assign_tasks_rule_based(project=self.project)
        hi.refresh_from_db()
        self.assertEqual(hi.status, TaskStatus.ASSIGNED)

    def test_no_active_annotators_assigns_nothing(self):
        make_volume_tasks(self.project, 4, shape=(64, 8, 8))
        result = assign_tasks_rule_based(project=self.project)
        self.assertEqual(result["assigned"], 0)
        self.assertEqual(calculate_annotator_workload(project=self.project), [])

    def test_even_distribution_across_annotators(self):
        # 4 tasks, 2 annotators -> 2 each (balanced, not piled on one).
        make_volume_tasks(self.project, 4, shape=(64, 8, 8))
        a = make_annotator("bal_a", max_active=10)
        b = make_annotator("bal_b", max_active=10)
        grant_assignment_team(self.project, [a, b])
        result = assign_tasks_rule_based(project=self.project)
        self.assertEqual(result["assigned"], 4)
        self.assertEqual(result["per_user"][a.id], 2)
        self.assertEqual(result["per_user"][b.id], 2)


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class AutoAssignVolumeTests(TestCase):
    def _volume(self, project, name, shape_z=32, label_type=LabelType.NONE):
        vol = register_volume(
            project=project, name=name, image_path=f"{name}.tiff",
            label_type=label_type, autodetect_shape=False,
        )
        vol.shape_x, vol.shape_y, vol.shape_z = 16, 16, shape_z
        vol.save()
        return vol

    def test_ensure_volume_tasks_one_task_per_volume(self):
        project = create_project(title="P", reviewed=True)
        self._volume(project, "v1")
        self._volume(project, "v2")
        result = ensure_volume_tasks(project)
        self.assertEqual(result["created"], 2)
        tasks = AnnotationTask.objects.filter(project=project)
        self.assertEqual(tasks.count(), 2)
        # Each task spans its whole volume (no frame splitting).
        for t in tasks:
            self.assertEqual(t.z_start, 0)
            self.assertEqual(t.z_end, t.volume.shape_z)

    def test_volume_without_shape_is_skipped(self):
        project = create_project(title="P", reviewed=True)
        register_volume(
            project=project, name="noshape", image_path="noshape.tiff",
            autodetect_shape=False,
        )
        result = ensure_volume_tasks(project)
        self.assertEqual(result, {"created": 0, "skipped": 1})

    def test_auto_assign_distributes_volumes_evenly(self):
        # 8 volumes, 4 annotators -> 2 volumes each.
        project = create_project(title="Eight", reviewed=True)
        for i in range(8):
            self._volume(project, f"vol{i}")
        annotators = [make_annotator(f"ann{i}", max_active=10) for i in range(4)]
        grant_assignment_team(project, annotators)

        summary = auto_assign_project(project)

        self.assertTrue(summary["reviewed"])
        self.assertEqual(summary["created_tasks"], 8)
        self.assertEqual(summary["assigned"], 8)
        for ann in annotators:
            self.assertEqual(summary["per_user"][ann.id], 2)

    def test_auto_assign_blocked_until_reviewed(self):
        project = create_project(title="Pending")  # reviewed defaults to False
        self._volume(project, "v1")
        make_annotator("solo", max_active=10)

        summary = auto_assign_project(project)

        self.assertFalse(summary["reviewed"])
        self.assertEqual(summary["assigned"], 0)
        self.assertEqual(AnnotationTask.objects.filter(project=project).count(), 0)


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class OneTaskPerVolumeInvariantTests(TestCase):
    """One volume is one assignable unit — nothing may subdivide it.

    This is the hard invariant the frame-splitting removal exists to protect,
    so it is asserted against the *outcome* (row counts and z extents) rather
    than against any one helper, and it re-runs every creation path that a
    manager can reach from the UI.
    """

    def _volume(self, project, name, shape_z=32):
        vol = register_volume(
            project=project, name=name, image_path=f"{name}.tiff",
            label_type=LabelType.NONE, autodetect_shape=False,
        )
        vol.shape_x, vol.shape_y, vol.shape_z = 16, 16, shape_z
        vol.save()
        return vol

    def _assert_invariant(self, project):
        for volume in project.volumes.all():
            tasks = list(volume.tasks.all())
            self.assertLessEqual(
                len(tasks), 1,
                f"volume {volume.name} carries {len(tasks)} tasks",
            )
            for task in tasks:
                self.assertEqual(task.z_start, 0)
                self.assertEqual(task.z_end, volume.shape_z)
                self.assertEqual((task.y_start, task.y_end), (0, volume.shape_y))
                self.assertEqual((task.x_start, task.x_end), (0, volume.shape_x))

    def test_every_creation_path_leaves_one_task_per_volume(self):
        project = create_project(title="Invariant", reviewed=True)
        for i in range(3):
            self._volume(project, f"v{i}", shape_z=16 + i)
        annotator = make_annotator("inv", max_active=10)
        grant_assignment_team(project, [annotator])

        # Every path a manager can reach, run twice, in sequence.
        for _ in range(2):
            ensure_volume_tasks(project)
            preview_assign_project(project)
            auto_assign_project(project)
            self._assert_invariant(project)

        self.assertEqual(AnnotationTask.objects.filter(project=project).count(), 3)

    def test_split_endpoint_is_not_registered(self):
        from django.urls import NoReverseMatch, reverse

        with self.assertRaises(NoReverseMatch):
            reverse("api-volume-split")
        project = create_project(title="No split", reviewed=True)
        volume = self._volume(project, "v")
        manager = User.objects.create_user("splitmgr", password="x", is_superuser=True)
        self.client.force_login(manager)
        response = self.client.post(f"/api/volumes/{volume.pk}/split/", {"z_step": 4})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(volume.tasks.count(), 0)


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class AssignmentPlanTests(TestCase):
    """The editable preview -> apply plan the manager curates."""

    def _volume(self, project, name):
        vol = register_volume(
            project=project, name=name, image_path=f"{name}.tiff",
            label_type=LabelType.NONE, autodetect_shape=False,
        )
        vol.shape_x, vol.shape_y, vol.shape_z = 16, 16, 32
        vol.save()
        return vol

    def test_preview_creates_tasks_but_does_not_assign(self):
        project = create_project(title="P", reviewed=True)
        self._volume(project, "v1")
        self._volume(project, "v2")
        ann = make_annotator("ann", max_active=10)
        grant_assignment_team(project, [ann])

        summary = preview_assign_project(project)

        self.assertTrue(summary["reviewed"])
        self.assertEqual(summary["created_tasks"], 2)
        # A plan was proposed for both tasks...
        self.assertEqual(len(summary["proposed"]), 2)
        self.assertTrue(all(v == ann.id for v in summary["proposed"].values()))
        # ...but nothing is actually assigned until the plan is applied.
        self.assertEqual(
            AnnotationTask.objects.filter(project=project).exclude(
                status=TaskStatus.UNASSIGNED
            ).count(),
            0,
        )

    def test_preview_blocked_until_reviewed(self):
        project = create_project(title="Pending")
        self._volume(project, "v1")
        summary = preview_assign_project(project)
        self.assertFalse(summary["reviewed"])
        self.assertEqual(AnnotationTask.objects.filter(project=project).count(), 0)

    def test_apply_plan_assigns_and_edits_fields(self):
        project = create_project(title="P", reviewed=True)
        self._volume(project, "v1")
        ensure_volume_tasks(project)
        task = AnnotationTask.objects.get(project=project)
        ann = make_annotator("ann", max_active=10)

        result = apply_assignment_plan(
            project,
            [
                {
                    "task_id": task.id,
                    "annotator_id": ann.id,
                    "priority": 7,
                    "difficulty": 3,
                    "instructions": "handle with care",
                    "deadline": None,
                }
            ],
            annotators_by_id={ann.id: ann},
        )

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["assigned"], 1)
        self.assertEqual(result["remaining_unassigned"], 0)
        task.refresh_from_db()
        self.assertEqual(task.assigned_to_id, ann.id)
        self.assertEqual(task.status, TaskStatus.ASSIGNED)
        self.assertEqual(task.priority, 7)
        self.assertEqual(task.difficulty, 3)
        self.assertEqual(task.instructions, "handle with care")

    def test_apply_plan_can_unassign(self):
        project = create_project(title="P", reviewed=True)
        self._volume(project, "v1")
        ensure_volume_tasks(project)
        task = AnnotationTask.objects.get(project=project)
        ann = make_annotator("ann", max_active=10)
        assign_task_to_annotator(task, annotator=ann)

        apply_assignment_plan(
            project,
            [{"task_id": task.id, "annotator_id": None}],
            annotators_by_id={},
        )

        task.refresh_from_db()
        self.assertIsNone(task.assigned_to_id)
        self.assertEqual(task.status, TaskStatus.UNASSIGNED)

    def test_apply_plan_rejects_foreign_task(self):
        project = create_project(title="P", reviewed=True)
        other = create_project(title="Other", reviewed=True)
        self._volume(other, "v1")
        ensure_volume_tasks(other)
        foreign_task = AnnotationTask.objects.get(project=other)

        with self.assertRaises(ValueError):
            apply_assignment_plan(
                project,
                [{"task_id": foreign_task.id, "annotator_id": None}],
                annotators_by_id={},
            )


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class TaskMetadataTests(TestCase):
    """Every role reads the same (dataset) metadata off a task."""

    def test_task_exposes_dataset_metadata_and_voxel(self):
        from projects.services import get_or_create_dataset

        from annotation.serializers import AnnotationTaskSerializer

        project = create_project(title="P", reviewed=True)
        meta = {"organism": "mouse", "tissue": "kidney"}
        dataset = get_or_create_dataset(project=project, name="DS", metadata=meta)
        vol = register_volume(
            project=project, dataset=dataset, name="v1",
            image_path="v1.tiff", autodetect_shape=False,
        )
        vol.shape_x, vol.shape_y, vol.shape_z = 32, 16, 8
        vol.voxel_size_x, vol.voxel_size_y, vol.voxel_size_z = 0.5, 0.25, 0.2
        vol.save()
        task = AnnotationTask.objects.create(
            project=project, volume=vol, z_start=0, z_end=8, y_end=16, x_end=32,
            task_type=TaskType.MANUAL_ANNOTATION,
        )

        data = AnnotationTaskSerializer(task).data
        # The annotator-facing payload carries the dataset's metadata verbatim,
        # so it matches what the manager sees on the dataset card.
        self.assertEqual(data["dataset_metadata"], meta)
        self.assertEqual(data["voxel_size_z"], 0.2)
        self.assertEqual(data["shape_x"], 32)
        self.assertEqual(data["file_format"], "tiff")
        self.assertFalse(data["has_region_mask"])
        self.assertEqual(data["dataset"], "DS")
