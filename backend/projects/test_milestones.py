"""Milestones and the delivery analytics that read them.

Progress is never stored, so the tests here mostly check that it is derived
correctly from the tasks it describes — including the cases where a stored
counter would have drifted.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import UserProfile
from annotation.models import AnnotationTask
from core.choices import (
    MilestoneMetric,
    TaskStatus,
    TaskType,
    UserRole,
    VolumeStatus,
)
from core.statistics import (
    attention_queue,
    burndown,
    milestone_progress,
    throughput_series,
)
from projects.models import Dataset, Milestone, Project
from volumes.models import Volume

ENABLED = override_settings(FEATURE_MILESTONES=True)


def make_user(username, role):
    user = User.objects.create_user(username=username, password="pw")
    UserProfile.objects.update_or_create(user=user, defaults={"role": role})
    # Refetch: the profile is auto-created with the default annotator role and
    # cached on the instance, so `update_or_create` changes the row while this
    # object still reports "annotator" — and every `is_manager` check reading
    # it would silently test the wrong role. Same trap documented in
    # volumes/test_chunk_service.py.
    return User.objects.get(pk=user.pk)


class MilestoneBaseTest(TestCase):
    def setUp(self):
        self.manager = make_user("mgr", UserRole.MANAGER)
        self.annotator = make_user("ann", UserRole.ANNOTATOR)
        self.project = Project.objects.create(title="P", created_by=self.manager)
        self.dataset = Dataset.objects.create(project=self.project, name="D")
        self.volumes = [
            Volume.objects.create(
                project=self.project, dataset=self.dataset, name=f"V{i}",
                shape_z=2, shape_y=2, shape_x=2,
            )
            for i in range(3)
        ]

    def make_task(self, volume, status=TaskStatus.ASSIGNED, **kwargs):
        return AnnotationTask.objects.create(
            project=self.project, volume=volume, assigned_to=self.annotator,
            z_start=0, z_end=2, y_end=2, x_end=2,
            task_type=TaskType.MANUAL_ANNOTATION, status=status, **kwargs
        )


class ProgressTests(MilestoneBaseTest):
    def test_progress_counts_approved_tasks(self):
        milestone = Milestone.objects.create(
            project=self.project, name="M", due_on=date.today() + timedelta(days=7),
            target_metric=MilestoneMetric.TASKS_APPROVED, target_value=2,
        )
        self.make_task(self.volumes[0], TaskStatus.APPROVED)
        self.make_task(self.volumes[1], TaskStatus.ASSIGNED)

        progress = milestone_progress(milestone)
        self.assertEqual(progress["achieved"], 1)
        self.assertEqual(progress["remaining"], 1)
        self.assertEqual(progress["percent_complete"], 50.0)
        self.assertFalse(progress["overdue"])

    def test_an_empty_volume_scope_means_the_whole_project(self):
        milestone = Milestone.objects.create(
            project=self.project, name="M", due_on=date.today() + timedelta(days=7),
            target_value=3,
        )
        for volume in self.volumes:
            self.make_task(volume, TaskStatus.APPROVED)
        self.assertEqual(milestone_progress(milestone)["achieved"], 3)

    def test_a_scoped_milestone_only_counts_its_own_volumes(self):
        milestone = Milestone.objects.create(
            project=self.project, name="M", due_on=date.today() + timedelta(days=7),
            target_value=1,
        )
        milestone.volumes.add(self.volumes[0])
        self.make_task(self.volumes[0], TaskStatus.APPROVED)
        self.make_task(self.volumes[1], TaskStatus.APPROVED)
        self.assertEqual(milestone_progress(milestone)["achieved"], 1)

    def test_a_zero_target_reports_no_percentage_rather_than_complete(self):
        """"No target set" and "target met" must not render the same."""
        milestone = Milestone.objects.create(
            project=self.project, name="M", due_on=date.today() + timedelta(days=1),
            target_value=0,
        )
        self.assertIsNone(milestone_progress(milestone)["percent_complete"])

    def test_a_past_due_date_with_work_left_is_overdue(self):
        milestone = Milestone.objects.create(
            project=self.project, name="M", due_on=date.today() - timedelta(days=1),
            target_value=5,
        )
        self.assertTrue(milestone_progress(milestone)["overdue"])

    def test_a_past_due_date_with_nothing_left_is_not_overdue(self):
        milestone = Milestone.objects.create(
            project=self.project, name="M", due_on=date.today() - timedelta(days=1),
            target_value=1,
        )
        self.make_task(self.volumes[0], TaskStatus.APPROVED)
        self.assertFalse(milestone_progress(milestone)["overdue"])

    def test_volumes_completed_is_a_different_metric(self):
        milestone = Milestone.objects.create(
            project=self.project, name="M", due_on=date.today() + timedelta(days=7),
            target_metric=MilestoneMetric.VOLUMES_COMPLETED, target_value=2,
        )
        self.volumes[0].status = VolumeStatus.COMPLETED
        self.volumes[0].save(update_fields=["status"])
        self.assertEqual(milestone_progress(milestone)["achieved"], 1)


class ThroughputTests(MilestoneBaseTest):
    def test_the_series_is_zero_filled_across_the_whole_window(self):
        """A chart must never guess whether a gap is "no work" or "no data"."""
        series = throughput_series(self.project, days=7)
        self.assertEqual(len(series["points"]), 7)
        self.assertTrue(all(point["approved"] == 0 for point in series["points"]))

    def test_an_approval_today_lands_on_todays_bucket(self):
        self.make_task(
            self.volumes[0], TaskStatus.APPROVED, approved_at=timezone.now()
        )
        series = throughput_series(self.project, days=7)
        self.assertEqual(series["points"][-1]["approved"], 1)

    def test_the_window_is_capped(self):
        self.assertLessEqual(len(throughput_series(self.project, days=9999)["points"]), 365)

    def test_burndown_ends_where_the_progress_card_says(self):
        """The two must agree; a chart disagreeing with its own header is worse
        than either being slightly wrong."""
        milestone = Milestone.objects.create(
            project=self.project, name="M", due_on=date.today() + timedelta(days=7),
            target_value=3,
        )
        self.make_task(
            self.volumes[0], TaskStatus.APPROVED, approved_at=timezone.now()
        )
        result = burndown(milestone, days=7)
        self.assertEqual(
            result["actual"][-1]["remaining"], result["milestone"]["remaining"]
        )


class AttentionQueueTests(MilestoneBaseTest):
    def test_an_overdue_unfinished_task_is_listed(self):
        self.make_task(
            self.volumes[0], TaskStatus.ASSIGNED,
            deadline=date.today() - timedelta(days=2),
        )
        queue = attention_queue(self.project)
        self.assertEqual(queue["overdue"]["count"], 1)

    def test_an_approved_task_is_never_overdue(self):
        """Approved work is not owed, whatever its deadline said."""
        self.make_task(
            self.volumes[0], TaskStatus.APPROVED,
            deadline=date.today() - timedelta(days=2),
        )
        self.assertEqual(attention_queue(self.project)["overdue"]["count"], 0)

    def test_rejected_work_is_still_owed(self):
        self.make_task(
            self.volumes[0], TaskStatus.REJECTED,
            deadline=date.today() - timedelta(days=2),
        )
        self.assertEqual(attention_queue(self.project)["overdue"]["count"], 1)

    def test_due_soon_and_overdue_do_not_double_count(self):
        self.make_task(
            self.volumes[0], TaskStatus.ASSIGNED, deadline=date.today()
        )
        queue = attention_queue(self.project)
        self.assertEqual(queue["overdue"]["count"], 0)
        self.assertEqual(queue["due_soon"]["count"], 1)


@ENABLED
class ApiTests(MilestoneBaseTest):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_a_manager_can_create_a_milestone(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            f"/api/projects/{self.project.pk}/milestones/",
            {"name": "Phase 1", "due_on": str(date.today() + timedelta(days=14)),
             "target_value": 5},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["progress"]["target_value"], 5)

    def test_an_annotator_may_read_but_not_write(self):
        self.make_task(self.volumes[0])  # makes them a project member
        self.client.force_authenticate(self.annotator)
        self.assertEqual(
            self.client.get(f"/api/projects/{self.project.pk}/milestones/").status_code,
            200,
        )
        response = self.client.post(
            f"/api/projects/{self.project.pk}/milestones/",
            {"name": "x", "due_on": str(date.today()), "target_value": 1},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_scoping_a_foreign_volume_is_refused(self):
        """Otherwise the milestone silently becomes unreachable: progress
        filters by project *and* by the scoped ids, so a foreign volume can
        never contribute."""
        other = Project.objects.create(title="Other", created_by=self.manager)
        foreign = Volume.objects.create(project=other, name="F")
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            f"/api/projects/{self.project.pk}/milestones/",
            {"name": "x", "due_on": str(date.today()), "target_value": 1,
             "volumes": [foreign.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_a_negative_target_is_refused(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            f"/api/projects/{self.project.pk}/milestones/",
            {"name": "x", "due_on": str(date.today()), "target_value": -3},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_the_delivery_endpoint_returns_all_three_panels(self):
        self.client.force_authenticate(self.manager)
        response = self.client.get(f"/api/projects/{self.project.pk}/delivery/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.data), {"throughput", "productivity", "attention"}
        )

    def test_disabled_reports_503_not_404(self):
        self.client.force_authenticate(self.manager)
        with override_settings(FEATURE_MILESTONES=False):
            response = self.client.get(f"/api/projects/{self.project.pk}/milestones/")
        self.assertEqual(response.status_code, 503)


@ENABLED
class DeliveryOverviewTests(MilestoneBaseTest):
    """``GET /api/delivery/`` — the cross-project attention queue."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_a_manager_sees_work_from_every_project(self):
        other_project = Project.objects.create(title="Other", created_by=self.manager)
        other_volume = Volume.objects.create(project=other_project, name="OV")
        AnnotationTask.objects.create(
            project=other_project, volume=other_volume, assigned_to=self.annotator,
            z_start=0, z_end=2, y_end=2, x_end=2,
            task_type=TaskType.MANUAL_ANNOTATION,
            deadline=date.today() - timedelta(days=1),
        )
        self.make_task(
            self.volumes[0], TaskStatus.ASSIGNED,
            deadline=date.today() - timedelta(days=1),
        )

        self.client.force_authenticate(self.manager)
        response = self.client.get("/api/delivery/")
        self.assertEqual(response.status_code, 200)
        # Both projects' overdue work, which the per-project endpoint cannot
        # show without opening each one in turn.
        self.assertEqual(response.data["attention"]["overdue"]["count"], 2)

    def test_an_annotator_is_refused_rather_than_quietly_filtered(self):
        """A filtered subset would read as "nothing is overdue" to somebody who
        simply cannot see it."""
        self.client.force_authenticate(self.annotator)
        response = self.client.get("/api/delivery/")
        self.assertEqual(response.status_code, 403)

    def test_it_returns_the_same_three_panels(self):
        self.client.force_authenticate(self.manager)
        response = self.client.get("/api/delivery/")
        self.assertEqual(
            set(response.data), {"throughput", "productivity", "attention"}
        )

    def test_disabled_reports_503_not_404(self):
        self.client.force_authenticate(self.manager)
        with override_settings(FEATURE_MILESTONES=False):
            response = self.client.get("/api/delivery/")
        self.assertEqual(response.status_code, 503)
