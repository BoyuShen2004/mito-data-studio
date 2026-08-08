"""Phase 6 — dashboards and statistics.

The property that matters most is **query count constant in row count**: a
dashboard that instantiates one object per counted row stops working exactly
when a project grows big enough to need a dashboard. `QueryCostTests` asserts
that directly rather than trusting the implementation to stay honest.
"""

from __future__ import annotations

import csv
import io

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import AnnotatorProfile, Institution, UserProfile
from annotation.models import AnnotationTask, ReviewRecord
from core.choices import (
    ReviewDecision,
    TaskStatus,
    UserRole,
)
from core.statistics import (
    annotator_statistics,
    dashboards_enabled,
    elapsed_durations,
    project_dashboard,
    rejection_rate,
    review_outcome_counts,
    task_status_counts,
)
from projects.models import Dataset, Project
from volumes.models import Volume

DASH_ON = override_settings(FEATURE_DASHBOARDS=True)
DASH_OFF = override_settings(FEATURE_DASHBOARDS=False)


def make_user(name, role=UserRole.ANNOTATOR):
    # get_or_create, not create: the query-cost tests rebuild the world more
    # than once inside a single test to compare small and large row counts.
    u, _ = User.objects.get_or_create(username=name)
    u.set_password("pw-for-tests-1")
    u.save()
    UserProfile.objects.update_or_create(user=u, defaults={"role": role})
    if role == UserRole.ANNOTATOR:
        AnnotatorProfile.objects.update_or_create(
            user=u, defaults={"is_active_annotator": True, "max_active_tasks": 50}
        )
    return u


class StatsFixtureMixin:
    def build(self, *, tasks=6):
        self.org, _ = Institution.objects.get_or_create(name="Stats Org")
        self.manager = make_user("st-mgr", UserRole.MANAGER)
        self.annotator = make_user("st-ann")
        self.project = Project.objects.create(
            title="Stats", created_by=self.manager, manager_reviewed=True
        )
        self.dataset = Dataset.objects.create(project=self.project, name="ds")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="v", image_path="a.tif"
        )
        self.tasks = [
            AnnotationTask.objects.create(
                project=self.project, volume=self.volume,
                z_start=i, z_end=i + 1, y_end=64, x_end=64,
                status=TaskStatus.UNASSIGNED,
            )
            for i in range(tasks)
        ]
        return self.tasks

    def complete(self, task, *, approved=True, minutes=30):
        """Give a task a full timestamp trail so durations are computable."""
        from datetime import timedelta

        now = timezone.now()
        task.assigned_to = self.annotator
        task.assigned_at = now - timedelta(minutes=minutes * 2)
        task.submitted_at = now - timedelta(minutes=minutes)
        task.status = TaskStatus.SUBMITTED
        if approved:
            task.approved_at = now
            task.status = TaskStatus.APPROVED
        task.save()
        return task


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


class TaskStatusCountTests(StatsFixtureMixin, TestCase):
    def setUp(self):
        self.build(tasks=5)

    def test_every_status_is_present_zero_filled(self):
        counts = task_status_counts(self.project)
        for s in TaskStatus.values:
            self.assertIn(s, counts)

    def test_counts_are_accurate(self):
        self.complete(self.tasks[0])
        self.complete(self.tasks[1])
        counts = task_status_counts(self.project)
        self.assertEqual(counts[TaskStatus.APPROVED], 2)
        self.assertEqual(counts[TaskStatus.UNASSIGNED], 3)

    def test_empty_project_is_all_zero(self):
        empty = Project.objects.create(title="empty")
        self.assertEqual(sum(task_status_counts(empty).values()), 0)

    def test_scoped_to_the_project(self):
        other = Project.objects.create(title="other")
        ds = Dataset.objects.create(project=other, name="d2")
        vol = Volume.objects.create(
            project=other, dataset=ds, name="v2", image_path="b.tif"
        )
        AnnotationTask.objects.create(
            project=other, volume=vol, z_start=0, z_end=1, y_end=64, x_end=64,
            status=TaskStatus.APPROVED,
        )
        self.assertEqual(task_status_counts(self.project)[TaskStatus.APPROVED], 0)
        self.assertEqual(task_status_counts(other)[TaskStatus.APPROVED], 1)

    def test_matches_legacy_python_tally(self):
        """The replaced loop and the grouped query must agree exactly."""
        self.complete(self.tasks[0])
        self.complete(self.tasks[1], approved=False)

        legacy = {s.value: 0 for s in TaskStatus}
        for t in self.project.tasks.only("status"):
            legacy[t.status] = legacy.get(t.status, 0) + 1

        self.assertEqual(task_status_counts(self.project), legacy)


class ElapsedDurationTests(StatsFixtureMixin, TestCase):
    def setUp(self):
        self.build(tasks=3)

    def test_none_when_nothing_has_progressed(self):
        d = elapsed_durations(self.project)
        self.assertIsNone(d["mean_elapsed_to_submit_seconds"])
        self.assertIsNone(d["mean_elapsed_to_approve_seconds"])

    def test_durations_are_computed(self):
        self.complete(self.tasks[0], minutes=30)
        d = elapsed_durations(self.project)
        # 30 minutes between assigned and submitted.
        self.assertAlmostEqual(d["mean_elapsed_to_submit_seconds"], 1800, delta=60)
        self.assertAlmostEqual(d["mean_elapsed_to_approve_seconds"], 1800, delta=60)

    def test_none_is_distinct_from_zero(self):
        """A stage nothing has reached must not render as 'took no time'."""
        self.complete(self.tasks[0], approved=False)
        d = elapsed_durations(self.project)
        self.assertIsNotNone(d["mean_elapsed_to_submit_seconds"])
        self.assertIsNone(d["mean_elapsed_to_approve_seconds"])


class ReviewOutcomeTests(StatsFixtureMixin, TestCase):
    def setUp(self):
        self.build(tasks=3)

    def test_rejection_rate_is_none_when_nothing_reviewed(self):
        self.assertIsNone(rejection_rate(review_outcome_counts(self.project)))

    def test_rejection_rate(self):
        for decision in (
            ReviewDecision.APPROVED,
            ReviewDecision.REJECTED,
            ReviewDecision.REVISION_REQUESTED,
            ReviewDecision.APPROVED,
        ):
            ReviewRecord.objects.create(
                task=self.tasks[0], reviewer=self.manager, decision=decision
            )
        outcomes = review_outcome_counts(self.project)
        self.assertEqual(sum(outcomes.values()), 4)
        self.assertEqual(rejection_rate(outcomes), 0.5)


# ---------------------------------------------------------------------------
# Query cost — the property ADR-004 is built on
# ---------------------------------------------------------------------------


class QueryCostTests(StatsFixtureMixin, TestCase):
    def _dashboard_queries(self, n_tasks):
        Project.objects.all().delete()
        self.build(tasks=n_tasks)
        for t in self.tasks[: n_tasks // 2]:
            self.complete(t)
        with CaptureQueriesContext(connection) as cap:
            project_dashboard(self.project)
        return len(cap.captured_queries)

    def test_dashboard_query_count_is_constant_in_row_count(self):
        small = self._dashboard_queries(4)
        large = self._dashboard_queries(60)
        self.assertEqual(
            small, large,
            f"dashboard query count grew with rows ({small} -> {large}); "
            "an aggregate has regressed to per-row work",
        )

    def test_status_counts_is_one_query(self):
        self.build(tasks=30)
        with CaptureQueriesContext(connection) as cap:
            task_status_counts(self.project)
        self.assertEqual(len(cap.captured_queries), 1)

    def test_annotator_statistics_query_count_is_constant(self):
        Project.objects.all().delete()
        self.build(tasks=4)
        for i in range(3):
            u = make_user(f"many-{i}")
            for t in self.tasks:
                AnnotationTask.objects.filter(pk=t.pk).update(assigned_to=u)
        with CaptureQueriesContext(connection) as cap:
            annotator_statistics()
        few = len(cap.captured_queries)

        for i in range(3, 12):
            make_user(f"many-{i}")
        with CaptureQueriesContext(connection) as cap:
            annotator_statistics()
        self.assertEqual(few, len(cap.captured_queries))

    def test_legacy_progress_no_longer_loads_rows(self):
        """The fixed calculate_project_progress must not scan tasks."""
        from projects.services import calculate_project_progress

        self.build(tasks=50)
        with CaptureQueriesContext(connection) as cap:
            calculate_project_progress(self.project)
        # One grouped status query plus the volumes count.
        self.assertLessEqual(
            len(cap.captured_queries), 3,
            f"progress used {len(cap.captured_queries)} queries",
        )


# ---------------------------------------------------------------------------
# Annotator statistics
# ---------------------------------------------------------------------------


class AnnotatorStatisticsTests(StatsFixtureMixin, TestCase):
    def setUp(self):
        self.build(tasks=6)

    def test_empty_roster(self):
        out = annotator_statistics()
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["results"], [])

    def test_counts_assigned_work(self):
        for t in self.tasks[:3]:
            AnnotationTask.objects.filter(pk=t.pk).update(assigned_to=self.annotator)
        out = annotator_statistics()
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["results"][0]["tasks_assigned"], 3)

    def test_pagination_bounds_results(self):
        for i in range(5):
            u = make_user(f"pg-{i}")
            AnnotationTask.objects.filter(pk=self.tasks[i].pk).update(assigned_to=u)
        page = annotator_statistics(limit=2, offset=0)
        self.assertEqual(len(page["results"]), 2)
        self.assertEqual(page["count"], 5)

    def test_limit_is_hard_capped(self):
        from core.statistics import MAX_ANNOTATORS

        out = annotator_statistics(limit=10**6)
        self.assertLessEqual(out["limit"], MAX_ANNOTATORS)

    def test_offset_past_the_end_is_empty_not_an_error(self):
        out = annotator_statistics(offset=10**6)
        self.assertEqual(out["results"], [])

    def test_scoped_by_project(self):
        other = Project.objects.create(title="other")
        ds = Dataset.objects.create(project=other, name="d2")
        vol = Volume.objects.create(
            project=other, dataset=ds, name="v2", image_path="b.tif"
        )
        AnnotationTask.objects.create(
            project=other, volume=vol, z_start=0, z_end=1, y_end=64, x_end=64,
            assigned_to=self.annotator, status=TaskStatus.ASSIGNED,
        )
        self.assertEqual(annotator_statistics(project=other)["count"], 1)
        self.assertEqual(annotator_statistics(project=self.project)["count"], 0)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


@DASH_OFF
class DashboardsDisabledTests(StatsFixtureMixin, TestCase):
    def setUp(self):
        self.build()
        self.client.force_login(self.manager)

    def test_disabled_by_default(self):
        self.assertFalse(dashboards_enabled())

    def test_project_stats_503(self):
        res = self.client.get(
            reverse("api-statistics-project", args=[self.project.pk])
        )
        self.assertEqual(res.status_code, 503)

    def test_export_503(self):
        res = self.client.get(
            reverse("api-statistics-project-export", args=[self.project.pk])
        )
        self.assertEqual(res.status_code, 503)

    def test_annotators_503(self):
        self.assertEqual(
            self.client.get(reverse("api-statistics-annotators")).status_code, 503
        )

    def test_route_exists_when_disabled(self):
        """A flag must not make the route look like a 404 typo."""
        self.assertNotEqual(
            self.client.get(
                reverse("api-statistics-project", args=[self.project.pk])
            ).status_code, 404,
        )


@DASH_ON
class DashboardApiTests(StatsFixtureMixin, TestCase):
    def setUp(self):
        self.build(tasks=4)
        self.complete(self.tasks[0])

    def test_manager_can_read_project_stats(self):
        self.client.force_login(self.manager)
        res = self.client.get(
            reverse("api-statistics-project", args=[self.project.pk])
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["tasks"]["total"], 4)
        self.assertEqual(body["tasks"]["approved"], 1)
        self.assertNotIn("instances", body)
        self.assertIn("mean_elapsed_to_submit_seconds", body["elapsed"])

    def test_anonymous_is_rejected(self):
        self.client.logout()
        res = self.client.get(
            reverse("api-statistics-project", args=[self.project.pk])
        )
        self.assertIn(res.status_code, (401, 403))

    def test_non_member_is_forbidden(self):
        outsider = make_user("outsider")
        self.client.force_login(outsider)
        res = self.client.get(
            reverse("api-statistics-project", args=[self.project.pk])
        )
        self.assertEqual(res.status_code, 403)

    def test_assigned_annotator_may_view(self):
        AnnotationTask.objects.filter(pk=self.tasks[1].pk).update(
            assigned_to=self.annotator
        )
        self.client.force_login(self.annotator)
        res = self.client.get(
            reverse("api-statistics-project", args=[self.project.pk])
        )
        self.assertEqual(res.status_code, 200)

    def test_missing_project_is_404(self):
        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.get(
                reverse("api-statistics-project", args=[999999])
            ).status_code, 404,
        )

    def test_annotator_stats_require_manager(self):
        self.client.force_login(self.annotator)
        self.assertEqual(
            self.client.get(reverse("api-statistics-annotators")).status_code, 403
        )

    def test_manager_reads_annotator_stats(self):
        self.client.force_login(self.manager)
        res = self.client.get(reverse("api-statistics-annotators"))
        self.assertEqual(res.status_code, 200)
        self.assertIn("results", res.json())

    def test_bad_pagination_is_400_not_500(self):
        self.client.force_login(self.manager)
        res = self.client.get(
            reverse("api-statistics-annotators"), {"limit": "abc"}
        )
        self.assertEqual(res.status_code, 400)


@DASH_ON
class CsvExportTests(StatsFixtureMixin, TestCase):
    def setUp(self):
        self.build(tasks=3)
        self.complete(self.tasks[0])
        self.client.force_login(self.manager)

    def test_csv_content_type_and_filename(self):
        res = self.client.get(
            reverse("api-statistics-project-export", args=[self.project.pk])
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/csv")
        self.assertIn("attachment", res["Content-Disposition"])

    def test_csv_has_header_and_one_row(self):
        res = self.client.get(
            reverse("api-statistics-project-export", args=[self.project.pk])
        )
        rows = list(csv.reader(io.StringIO(res.content.decode())))
        self.assertEqual(len(rows), 2)
        self.assertIn("project_id", rows[0])

    def test_csv_agrees_with_json(self):
        """A spreadsheet disagreeing with the screen is worse than either."""
        j = self.client.get(
            reverse("api-statistics-project", args=[self.project.pk])
        ).json()
        c = self.client.get(
            reverse("api-statistics-project-export", args=[self.project.pk])
        )
        rows = list(csv.reader(io.StringIO(c.content.decode())))
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(int(row["tasks_total"]), j["tasks"]["total"])
        self.assertEqual(int(row["tasks_approved"]), j["tasks"]["approved"])
        self.assertEqual(
            float(row["percent_complete"]), j["tasks"]["percent_complete"]
        )

    def test_csv_forbidden_for_non_member(self):
        self.client.force_login(make_user("nope"))
        self.assertEqual(
            self.client.get(
                reverse("api-statistics-project-export", args=[self.project.pk])
            ).status_code, 403,
        )


# ---------------------------------------------------------------------------
# Interaction with Phases 1-5
# ---------------------------------------------------------------------------


@DASH_ON
class PhaseInteractionTests(StatsFixtureMixin, TestCase):
    def setUp(self):
        self.build(tasks=4)

    def test_dashboard_works_with_all_other_flags_off(self):
        d = project_dashboard(self.project)
        self.assertEqual(d["tasks"]["total"], 4)

    @override_settings(FEATURE_TEAMS=True)
    def test_team_member_may_view_project_stats(self):
        from accounts.models import Team
        from accounts.teams import add_team_member, grant_project_team

        outsider = make_user("teamer")
        team = Team.objects.create(organization=self.org, name="T")
        add_team_member(team, outsider)
        grant_project_team(self.project, team)
        self.client.force_login(outsider)
        res = self.client.get(
            reverse("api-statistics-project", args=[self.project.pk])
        )
        self.assertEqual(res.status_code, 200)

    def test_paused_project_still_reports(self):
        self.project.paused = True
        self.project.save(update_fields=["paused"])
        d = project_dashboard(self.project)
        self.assertTrue(d["project"]["paused"])
        self.assertEqual(d["tasks"]["total"], 4)

    def test_deleted_task_drops_out_of_the_aggregate(self):
        AnnotationTask.objects.filter(pk=self.tasks[0].pk).delete()
        self.assertEqual(project_dashboard(self.project)["tasks"]["total"], 3)
