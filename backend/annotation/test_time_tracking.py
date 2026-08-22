"""Automatic annotation time tracking.

Time is never taken from the client and never advanced by a real ``sleep``:
every test drives a frozen server clock, so a heartbeat "two minutes later" is
exact rather than approximately right and the suite stays fast.

The cases are grouped the way the feature is specified — rollout eligibility,
lifecycle, double-counting, submission/assignment, reporting, and the promise
that none of it can break annotation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AnnotatorProfile, UserProfile
from annotation import timing
from annotation.models import AnnotationTask, WorkInterval, WorkSession
from annotation.services import assign_task_to_annotator
from core.choices import TaskStatus, TimeTracking, UserRole
from projects.models import Dataset, Project
from volumes.models import Volume

User = get_user_model()

# A fixed, timezone-aware origin. Every test measures from here.
T0 = datetime(2026, 3, 2, 9, 0, 0, tzinfo=dt_timezone.utc)


class FrozenClock:
    """A stand-in for ``timezone.now`` that only moves when a test says so."""

    def __init__(self, start=T0):
        self.now = start

    def __call__(self):
        return self.now

    def tick(self, seconds: float):
        self.now = self.now + timedelta(seconds=seconds)
        return self.now


def frozen(clock: FrozenClock):
    """Patch the *one* clock every timing code path reads."""
    return mock.patch("django.utils.timezone.now", clock)


def make_user(username, role=UserRole.ANNOTATOR):
    user = User.objects.create_user(username, password="x")
    UserProfile.objects.filter(user=user).update(role=role)
    if role == UserRole.ANNOTATOR:
        AnnotatorProfile.objects.create(user=user, is_active_annotator=True)
    return User.objects.get(pk=user.pk)


class TimingFixture:
    """One project, one dataset, one eligible volume, one assigned task."""

    def build(self):
        self.manager = make_user("tt-mgr", UserRole.MANAGER)
        self.annotator = make_user("tt-ann")
        self.other = make_user("tt-other")
        self.requester = make_user("tt-req", UserRole.REQUESTER)
        self.project = Project.objects.create(
            title="Timing", created_by=self.manager, manager_reviewed=True
        )
        self.dataset = Dataset.objects.create(project=self.project, name="ds-a")
        self.volume = self.make_volume("v-a")
        self.task = self.make_task(self.volume, assigned_to=self.annotator)

    def make_volume(self, name, *, dataset=None, eligible=True):
        return Volume.objects.create(
            project=self.project,
            dataset=dataset or self.dataset,
            name=name,
            image_path=f"{name}.tif",
            time_tracking=(
                TimeTracking.ELIGIBLE if eligible else TimeTracking.LEGACY_EXEMPT
            ),
        )

    def make_task(self, volume, *, assigned_to=None):
        return AnnotationTask.objects.create(
            project=self.project,
            volume=volume,
            z_start=0,
            z_end=4,
            y_end=32,
            x_end=32,
            assigned_to=assigned_to,
            status=TaskStatus.ASSIGNED if assigned_to else TaskStatus.UNASSIGNED,
        )

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    # -- protocol helpers ---------------------------------------------------

    def start(self, clock, *, task=None, actor=None, token="tab-1"):
        with frozen(clock):
            return timing.start_timing(
                task=task or self.task,
                actor=actor or self.annotator,
                client_token=token,
            )

    def beat(self, clock, session, *, task=None, actor=None):
        with frozen(clock):
            return timing.heartbeat_timing(
                session_id=session.id, actor=actor or self.annotator,
                task=task or self.task,
            )

    def stop(self, clock, session, *, actor=None, reason="ended"):
        with frozen(clock):
            return timing.stop_timing(
                session_id=session.id, actor=actor or self.annotator, reason=reason
            )

    def seconds(self, clock, *, task=None):
        with frozen(clock):
            return timing.task_time(task or self.task)["seconds"]


# ---------------------------------------------------------------------------
# 1-3: rollout eligibility
# ---------------------------------------------------------------------------


class RolloutClassificationTests(TimingFixture, TestCase):
    """The migration's real function, run against the real models.

    Calling ``classify_existing_volumes`` directly rather than replaying the
    migration keeps the test honest about *behaviour* (which volumes end up
    exempt) without depending on migration replay machinery. The fields it
    touches are identical in the historical and current model states.
    """

    def setUp(self):
        self.build()

    def _classify(self):
        """Run the migration's own classifier against the live models.

        ``import_module`` rather than ``import`` because the module name starts
        with a digit and is therefore not a legal identifier.
        """
        import importlib

        module = importlib.import_module(
            "volumes.migrations.0015_time_tracking_rollout"
        )
        module.classify_existing_volumes(django_apps, None)

    def test_assigned_volume_at_rollout_becomes_legacy_exempt(self):
        assigned = self.make_volume("already-assigned")
        self.make_task(assigned, assigned_to=self.annotator)
        Volume.objects.filter(pk=assigned.pk).update(time_tracking_set_at=None)

        self._classify()

        assigned.refresh_from_db()
        self.assertEqual(assigned.time_tracking, TimeTracking.LEGACY_EXEMPT)
        self.assertIsNotNone(assigned.time_tracking_set_at)
        self.assertEqual(
            assigned.time_tracking_reason, "rollout_assigned_at_launch"
        )
        self.assertFalse(timing.volume_is_eligible(assigned))

    def test_unassigned_volume_at_rollout_stays_eligible(self):
        idle = self.make_volume("never-assigned")
        self.make_task(idle, assigned_to=None)
        Volume.objects.filter(pk=idle.pk).update(time_tracking_set_at=None)

        self._classify()

        idle.refresh_from_db()
        self.assertEqual(idle.time_tracking, TimeTracking.ELIGIBLE)
        self.assertEqual(idle.time_tracking_reason, "rollout_unassigned_at_launch")
        self.assertTrue(timing.volume_is_eligible(idle))

    def test_a_volume_with_no_tasks_at_all_is_eligible(self):
        bare = self.make_volume("no-tasks")
        Volume.objects.filter(pk=bare.pk).update(time_tracking_set_at=None)
        self._classify()
        bare.refresh_from_db()
        self.assertEqual(bare.time_tracking, TimeTracking.ELIGIBLE)

    def test_rollout_is_rerunnable_and_does_not_reclassify(self):
        """A second run must not undo a later administrative decision."""
        volume = self.make_volume("hand-set")
        Volume.objects.filter(pk=volume.pk).update(time_tracking_set_at=None)
        self._classify()
        # An administrator later exempts it by hand, with its own reason.
        Volume.objects.filter(pk=volume.pk).update(
            time_tracking=TimeTracking.LEGACY_EXEMPT,
            time_tracking_reason="manual_admin_decision",
        )
        # Assigning it now would have made a naive rerun reclassify it.
        self.make_task(volume, assigned_to=self.annotator)

        self._classify()

        volume.refresh_from_db()
        self.assertEqual(volume.time_tracking, TimeTracking.LEGACY_EXEMPT)
        self.assertEqual(volume.time_tracking_reason, "manual_admin_decision")

    def test_newly_registered_volumes_default_to_eligible(self):
        fresh = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="fresh",
            image_path="fresh.tif",
        )
        self.assertEqual(fresh.time_tracking, TimeTracking.ELIGIBLE)
        self.assertIsNone(fresh.time_tracking_set_at)
        self.assertTrue(timing.volume_is_eligible(fresh))

    def test_eligibility_never_follows_current_assignment(self):
        """An exempt volume stays exempt after being reassigned.

        This is the reason eligibility is stored rather than derived: a live
        "is it assigned?" query would silently promote this volume and start
        reporting a fraction of its real effort as the whole of it.
        """
        exempt = self.make_volume("exempt", eligible=False)
        task = self.make_task(exempt, assigned_to=None)
        assign_task_to_annotator(task, annotator=self.annotator)
        exempt.refresh_from_db()
        self.assertEqual(exempt.time_tracking, TimeTracking.LEGACY_EXEMPT)
        self.assertFalse(timing.volume_is_eligible(exempt))


# ---------------------------------------------------------------------------
# 4-8: who and what may be timed
# ---------------------------------------------------------------------------


class EligibilityAndPermissionTests(TimingFixture, TestCase):
    def setUp(self):
        self.build()

    def test_legacy_exempt_task_reports_unknown_not_zero(self):
        exempt = self.make_volume("legacy", eligible=False)
        task = self.make_task(exempt, assigned_to=self.annotator)
        summary = timing.task_time(task)
        self.assertFalse(summary["tracked"])
        self.assertIsNone(summary["seconds"])
        self.assertEqual(summary["display"], "-")

    def test_legacy_exempt_task_cannot_start_a_session(self):
        exempt = self.make_volume("legacy", eligible=False)
        task = self.make_task(exempt, assigned_to=self.annotator)
        with self.assertRaises(timing.TimingError) as ctx:
            timing.start_timing(task=task, actor=self.annotator, client_token="t")
        self.assertEqual(ctx.exception.reason, "legacy_exempt")
        self.assertEqual(WorkSession.objects.filter(task=task).count(), 0)

    def test_unassigned_eligible_volume_does_not_time_from_a_view(self):
        idle = self.make_volume("idle")
        task = self.make_task(idle, assigned_to=None)
        allowed, reason = timing.can_track_task(self.annotator, task)
        self.assertFalse(allowed)
        self.assertEqual(reason, "not_assigned")
        # And a status read (what a viewer does) creates nothing.
        response = self.client_for(self.manager).get(f"/api/tasks/{task.id}/timing/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["tracking"])
        self.assertEqual(WorkSession.objects.count(), 0)

    def test_assigned_annotator_on_an_eligible_task_may_time(self):
        allowed, reason = timing.can_track_task(self.annotator, self.task)
        self.assertTrue(allowed)
        self.assertEqual(reason, "ok")

    def test_manager_viewing_does_not_accrue_annotator_time(self):
        """A manager may edit any task, so `can_edit_task` is not the gate."""
        from annotation.services import can_edit_task

        self.assertTrue(can_edit_task(self.manager, self.task))
        allowed, reason = timing.can_track_task(self.manager, self.task)
        self.assertFalse(allowed)
        self.assertEqual(reason, "not_assigned")

        response = self.client_for(self.manager).post(
            f"/api/tasks/{self.task.id}/timing/start/", {"client_token": "m"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["tracking"])
        self.assertEqual(WorkSession.objects.count(), 0)

    def test_requester_cannot_time(self):
        allowed, _ = timing.can_track_task(self.requester, self.task)
        self.assertFalse(allowed)

    def test_a_locked_task_stops_being_timed(self):
        AnnotationTask.objects.filter(pk=self.task.pk).update(annotation_locked=True)
        self.task.refresh_from_db()
        allowed, reason = timing.can_track_task(self.annotator, self.task)
        self.assertFalse(allowed)
        self.assertEqual(reason, "not_editable")

    def test_another_annotator_cannot_start_on_this_task(self):
        response = self.client_for(self.other).post(
            f"/api/tasks/{self.task.id}/timing/start/", {"client_token": "x"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["tracking"])
        self.assertEqual(WorkSession.objects.count(), 0)

    def test_another_annotator_cannot_heartbeat_someone_elses_session(self):
        clock = FrozenClock()
        session = self.start(clock)
        response = self.client_for(self.other).post(
            f"/api/tasks/{self.task.id}/timing/heartbeat/",
            {"session_id": str(session.id)},
            format="json",
        )
        # Not their task at all, so they never reach the session.
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["tracking"])
        session.refresh_from_db()
        self.assertEqual(session.heartbeats, 0)

    def test_another_annotator_holding_the_task_cannot_touch_a_prior_session(self):
        """Reassignment must not hand the new annotator the old session."""
        clock = FrozenClock()
        session = self.start(clock)
        assign_task_to_annotator(self.task, annotator=self.other)
        self.task.refresh_from_db()
        with self.assertRaises(timing.TimingError) as ctx:
            timing.heartbeat_timing(
                session_id=session.id, actor=self.other, task=self.task
            )
        self.assertEqual(ctx.exception.reason, "forbidden")

    def test_anonymous_is_never_the_timing_annotator(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(timing.is_timing_annotator(AnonymousUser(), self.task))


# ---------------------------------------------------------------------------
# 9-15: lifecycle, idempotency and double-counting
# ---------------------------------------------------------------------------


class LifecycleTests(TimingFixture, TestCase):
    def setUp(self):
        self.build()

    def test_a_session_starts_with_an_open_interval_and_no_time(self):
        clock = FrozenClock()
        session = self.start(clock)
        self.assertTrue(session.is_open)
        self.assertEqual(self.seconds(clock), 0)
        self.assertEqual(WorkInterval.objects.filter(session=session).count(), 1)

    def test_heartbeats_credit_server_measured_time_only(self):
        clock = FrozenClock()
        session = self.start(clock)
        clock.tick(30)
        self.beat(clock, session)
        clock.tick(30)
        self.beat(clock, session)
        self.assertEqual(self.seconds(clock), 60)

    def test_a_client_supplied_duration_is_ignored(self):
        """The wire format has no duration field, and inventing one changes nothing."""
        clock = FrozenClock()
        session = self.start(clock)
        clock.tick(30)
        with frozen(clock):
            response = self.client_for(self.annotator).post(
                f"/api/tasks/{self.task.id}/timing/heartbeat/",
                {
                    "session_id": str(session.id),
                    "elapsed_seconds": 99999,
                    "started_at": "2000-01-01T00:00:00Z",
                },
                format="json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_seconds"], 30)

    def test_stop_is_idempotent(self):
        clock = FrozenClock()
        session = self.start(clock)
        clock.tick(45)
        self.stop(clock, session)
        first = self.seconds(clock)
        clock.tick(600)
        self.stop(clock, session)
        self.stop(clock, session)
        self.assertEqual(self.seconds(clock), first)
        self.assertEqual(first, 45)

    def test_a_retried_start_resumes_rather_than_opening_a_second_session(self):
        clock = FrozenClock()
        first = self.start(clock, token="tab-1")
        clock.tick(30)
        second = self.start(clock, token="tab-1")
        self.assertEqual(first.id, second.id)
        self.assertEqual(WorkSession.objects.filter(task=self.task).count(), 1)
        self.assertEqual(self.seconds(clock), 30)

    def test_duplicate_heartbeats_do_not_double_count(self):
        clock = FrozenClock()
        session = self.start(clock)
        clock.tick(30)
        self.beat(clock, session)
        # Same instant, three more times — a retrying client.
        self.beat(clock, session)
        self.beat(clock, session)
        self.beat(clock, session)
        self.assertEqual(self.seconds(clock), 30)

    def test_two_tabs_do_not_double_count_overlapping_time(self):
        clock = FrozenClock()
        tab_a = self.start(clock, token="tab-a")
        tab_b = self.start(clock, token="tab-b")
        self.assertNotEqual(tab_a.id, tab_b.id)
        for _ in range(4):
            clock.tick(30)
            self.beat(clock, tab_a)
            self.beat(clock, tab_b)
        # Two sessions each counted 120 s of their own...
        tab_a.refresh_from_db()
        tab_b.refresh_from_db()
        self.assertEqual(tab_a.active_seconds + tab_b.active_seconds, 240)
        # ...but only two minutes of wall clock actually passed.
        self.assertEqual(self.seconds(clock), 120)

    def test_two_devices_overlapping_partially_count_the_union(self):
        clock = FrozenClock()
        laptop = self.start(clock, token="laptop")
        for _ in range(2):  # 0 → 60
            clock.tick(30)
            self.beat(clock, laptop)
        desktop = self.start(clock, token="desktop")  # joins at 60
        for _ in range(2):  # 60 → 120
            clock.tick(30)
            self.beat(clock, laptop)
            self.beat(clock, desktop)
        self.stop(clock, laptop)
        for _ in range(2):  # 120 → 180, desktop alone
            clock.tick(30)
            self.beat(clock, desktop)
        self.assertEqual(self.seconds(clock), 180)

    def test_a_refresh_neither_loses_time_nor_double_counts(self):
        clock = FrozenClock()
        session = self.start(clock, token="tab-1")
        clock.tick(30)
        self.beat(clock, session)
        # The tab reloads: it stops (best effort) and starts again with the
        # same stored token.
        self.stop(clock, session, reason="unload")
        clock.tick(5)
        resumed = self.start(clock, token="tab-1")
        self.assertNotEqual(resumed.id, session.id)  # the old one really closed
        clock.tick(30)
        self.beat(clock, resumed)
        # 30 before the refresh + 30 after, and the 5 s reload gap counted once
        # at most — never twice, and never lost entirely.
        total = self.seconds(clock)
        self.assertGreaterEqual(total, 60)
        self.assertLessEqual(total, 65)

    def test_a_refresh_that_never_stopped_still_does_not_double_count(self):
        """`beforeunload` is unreliable, so the stop may simply never arrive."""
        clock = FrozenClock()
        session = self.start(clock, token="tab-1")
        clock.tick(30)
        self.beat(clock, session)
        clock.tick(5)
        resumed = self.start(clock, token="tab-1")
        self.assertEqual(resumed.id, session.id)
        clock.tick(30)
        self.beat(clock, resumed)
        self.assertEqual(self.seconds(clock), 65)

    def test_a_hidden_tab_stops_counting_when_it_stops_heartbeating(self):
        clock = FrozenClock()
        session = self.start(clock)
        clock.tick(30)
        self.beat(clock, session)
        # The tab is hidden; the client stops beating. Eight hours pass.
        clock.tick(8 * 3600)
        self.assertEqual(self.seconds(clock), 30)

    def test_an_abandoned_session_is_capped_at_its_last_heartbeat(self):
        clock = FrozenClock()
        session = self.start(clock)
        clock.tick(30)
        self.beat(clock, session)
        clock.tick(7 * 24 * 3600)  # the browser crashed a week ago
        self.assertEqual(self.seconds(clock), 30)
        # Reconciliation writes the same answer down; it cannot change it.
        with frozen(clock):
            self.assertEqual(timing.reconcile_abandoned(), 1)
        self.assertEqual(self.seconds(clock), 30)
        session.refresh_from_db()
        self.assertFalse(session.is_open)
        self.assertEqual(session.close_reason, WorkInterval.CloseReason.EXPIRED)

    @override_settings(MITO_TIME_TRACKING_ABANDON_GRACE_SECONDS=45)
    def test_the_abandon_grace_is_configurable(self):
        clock = FrozenClock()
        session = self.start(clock)
        clock.tick(30)
        self.beat(clock, session)
        clock.tick(10 * 3600)
        self.assertEqual(self.seconds(clock), 30 + 45)

    def test_an_idle_gap_inside_a_session_is_not_work(self):
        """Lunch is a hole in the interval, not an interval through lunch."""
        clock = FrozenClock()
        session = self.start(clock)
        clock.tick(30)
        self.beat(clock, session)
        clock.tick(3600)  # away, well past the idle timeout
        self.beat(clock, session)
        clock.tick(30)
        self.beat(clock, session)
        self.assertEqual(self.seconds(clock), 60)
        self.assertEqual(WorkInterval.objects.filter(session=session).count(), 2)

    def test_a_sleeping_tab_credits_the_cap_not_the_sleep(self):
        clock = FrozenClock()
        session = self.start(clock)
        clock.tick(200)  # between the 120 s cap and the 300 s idle timeout
        self.beat(clock, session)
        self.assertEqual(self.seconds(clock), 120)
        # And the un-credited 80 s is outside every interval, not inside one.
        self.assertEqual(WorkInterval.objects.filter(session=session).count(), 2)

    def test_a_backwards_clock_credits_nothing(self):
        clock = FrozenClock()
        session = self.start(clock)
        clock.tick(-60)
        self.beat(clock, session)
        self.assertGreaterEqual(self.seconds(clock), 0)

    def test_heartbeating_a_closed_session_reports_rather_than_raises_to_the_client(self):
        clock = FrozenClock()
        session = self.start(clock)
        self.stop(clock, session)
        with frozen(clock):
            response = self.client_for(self.annotator).post(
                f"/api/tasks/{self.task.id}/timing/heartbeat/",
                {"session_id": str(session.id)}, format="json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["tracking"])
        self.assertEqual(response.json()["reason"], "closed")

    def test_status_is_side_effect_free(self):
        response = self.client_for(self.annotator).get(
            f"/api/tasks/{self.task.id}/timing/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WorkSession.objects.count(), 0)
        self.assertEqual(response.json()["display"], "0m")
        self.assertTrue(response.json()["eligible"])

    def test_status_serves_the_protocol_constants(self):
        config = self.client_for(self.annotator).get(
            f"/api/tasks/{self.task.id}/timing/"
        ).json()["config"]
        self.assertEqual(config, timing.timing_config())
        # The cadence must stay below the server's per-interval cap, or every
        # ordinary heartbeat would silently discard real work.
        self.assertLess(config["heartbeat_seconds"], config["max_interval_seconds"])


# ---------------------------------------------------------------------------
# 16-19: submission and assignment
# ---------------------------------------------------------------------------


class SubmissionAndAssignmentTests(TimingFixture, TestCase):
    def setUp(self):
        self.build()

    def test_submit_closes_the_interval_and_keeps_the_total(self):
        clock = FrozenClock()
        session = self.start(clock)
        clock.tick(60)
        self.beat(clock, session)
        with frozen(clock):
            timing.stop_task_timing(
                self.task, actor=self.annotator,
                reason=WorkInterval.CloseReason.SUBMITTED,
            )
        session.refresh_from_db()
        self.assertFalse(session.is_open)
        self.assertEqual(session.close_reason, WorkInterval.CloseReason.SUBMITTED)
        clock.tick(9999)
        self.assertEqual(self.seconds(clock), 60)

    def test_reopening_and_submitting_again_adds_to_the_total(self):
        clock = FrozenClock()
        first = self.start(clock, token="round-1")
        clock.tick(60)
        self.beat(clock, first)
        with frozen(clock):
            timing.stop_task_timing(
                self.task, reason=WorkInterval.CloseReason.SUBMITTED
            )
        self.assertEqual(self.seconds(clock), 60)

        clock.tick(86400)  # the manager sends it back the next day
        second = self.start(clock, token="round-2")
        clock.tick(90)
        self.beat(clock, second)
        with frozen(clock):
            timing.stop_task_timing(
                self.task, reason=WorkInterval.CloseReason.SUBMITTED
            )
        self.assertEqual(self.seconds(clock), 150)

    def test_reassignment_keeps_history_with_its_original_annotator(self):
        clock = FrozenClock()
        session = self.start(clock)
        clock.tick(120)
        self.beat(clock, session)

        with frozen(clock):
            assign_task_to_annotator(self.task, annotator=self.other)
        self.task.refresh_from_db()

        # The first annotator's session was closed by the handover...
        session.refresh_from_db()
        self.assertFalse(session.is_open)
        self.assertEqual(session.close_reason, WorkInterval.CloseReason.SUPERSEDED)
        # ...their 120 s remains theirs...
        with frozen(clock):
            self.assertEqual(
                timing.task_time(self.task, actor=self.annotator)["seconds"], 120
            )
            self.assertEqual(
                timing.task_time(self.task, actor=self.other)["seconds"], 0
            )
        # ...and the task total is still the union of everyone's work.
        self.assertEqual(self.seconds(clock), 120)

        # The new annotator now accrues their own.
        new_session = self.start(clock, actor=self.other, token="tab-2")
        clock.tick(60)
        self.beat(clock, new_session, actor=self.other)
        with frozen(clock):
            self.assertEqual(
                timing.task_time(self.task, actor=self.annotator)["seconds"], 120
            )
            self.assertEqual(
                timing.task_time(self.task, actor=self.other)["seconds"], 60
            )
        self.assertEqual(self.seconds(clock), 180)

    def test_losing_the_assignment_stops_further_accumulation(self):
        clock = FrozenClock()
        session = self.start(clock)
        clock.tick(30)
        self.beat(clock, session)

        with frozen(clock):
            assign_task_to_annotator(self.task, annotator=None)
        self.task.refresh_from_db()

        clock.tick(30)
        with self.assertRaises(timing.TimingError) as ctx:
            self.beat(clock, session)
        self.assertEqual(ctx.exception.reason, "not_assigned")
        self.assertEqual(self.seconds(clock), 30)


# ---------------------------------------------------------------------------
# 20-23: reporting and drill-down
# ---------------------------------------------------------------------------


class ReportingTests(TimingFixture, TestCase):
    def setUp(self):
        self.build()
        self.dataset_b = Dataset.objects.create(project=self.project, name="ds-b")
        self.volume_b = self.make_volume("v-b", dataset=self.dataset_b)
        self.task_b = self.make_task(self.volume_b, assigned_to=self.annotator)
        self.legacy_volume = self.make_volume("v-legacy", eligible=False)
        self.legacy_task = self.make_task(
            self.legacy_volume, assigned_to=self.annotator
        )

    def _work(self, clock, task, seconds, *, actor=None, token="t"):
        """Work for ``seconds``, heartbeating the way a real client does.

        The cadence matters: a single heartbeat an hour later is an *idle gap*
        and correctly credits nothing, so a fixture that ticks the whole
        duration in one step would measure zero and prove nothing. Steps of one
        cap-length are the longest interval that is still credited in full.
        """
        step = timing.max_interval_seconds()
        session = self.start(clock, task=task, actor=actor, token=token)
        remaining = seconds
        while remaining > 0:
            clock.tick(min(step, remaining))
            self.beat(clock, session, task=task, actor=actor)
            remaining -= min(step, remaining)
        self.stop(clock, session, actor=actor)
        return session

    def _report(self, clock, actor=None):
        with frozen(clock):
            return timing.annotator_time_report(actor or self.annotator)

    def test_project_total_sums_the_annotators_eligible_sessions(self):
        clock = FrozenClock()
        self._work(clock, self.task, 60, token="a")
        clock.tick(10)
        self._work(clock, self.task_b, 120, token="b")
        report = self._report(clock)
        self.assertEqual(report["seconds"], 180)
        self.assertEqual(report["display"], "3m")
        self.assertEqual(len(report["projects"]), 1)
        self.assertEqual(report["projects"][0]["seconds"], 180)

    def test_dataset_drill_down_totals_are_correct(self):
        clock = FrozenClock()
        self._work(clock, self.task, 60, token="a")
        clock.tick(10)
        self._work(clock, self.task_b, 3600, token="b")
        datasets = {
            row["dataset_name"]: row
            for row in self._report(clock)["projects"][0]["datasets"]
        }
        self.assertEqual(datasets["ds-a"]["seconds"], 60)
        self.assertEqual(datasets["ds-a"]["display"], "1m")
        self.assertEqual(datasets["ds-b"]["seconds"], 3600)
        self.assertEqual(datasets["ds-b"]["display"], "1h")

    def test_volume_drill_down_shows_exact_values_and_a_dash_for_legacy(self):
        clock = FrozenClock()
        self._work(clock, self.task, 137 * 60, token="a")
        report = self._report(clock)
        volumes = {
            volume["volume_name"]: volume
            for dataset in report["projects"][0]["datasets"]
            for volume in dataset["volumes"]
        }
        self.assertEqual(volumes["v-a"]["display"], "2h 17m")
        self.assertTrue(volumes["v-a"]["tracked"])
        # Eligible but never worked on: a real zero.
        self.assertEqual(volumes["v-b"]["display"], "0m")
        self.assertEqual(volumes["v-b"]["seconds"], 0)
        # Legacy: unknown, and emphatically not zero.
        self.assertFalse(volumes["v-legacy"]["tracked"])
        self.assertIsNone(volumes["v-legacy"]["seconds"])
        self.assertEqual(volumes["v-legacy"]["display"], "-")

    def test_a_mixed_total_declares_that_it_is_incomplete(self):
        clock = FrozenClock()
        self._work(clock, self.task, 60, token="a")
        report = self._report(clock)
        project = report["projects"][0]
        self.assertEqual(project["seconds"], 60)
        # The number is real, but it is not the whole story, and the payload
        # says so rather than letting the UI imply completeness.
        self.assertTrue(project["has_legacy"])
        self.assertEqual(project["legacy_volumes"], 1)
        self.assertTrue(report["has_legacy"])
        legacy_dataset = next(
            row for row in project["datasets"] if row["dataset_name"] == "ds-a"
        )
        self.assertEqual(legacy_dataset["legacy_volumes"], 1)

    def test_a_fully_measured_project_is_not_marked_incomplete(self):
        self.legacy_task.delete()
        self.legacy_volume.delete()
        clock = FrozenClock()
        self._work(clock, self.task, 60, token="a")
        report = self._report(clock)
        self.assertFalse(report["has_legacy"])
        self.assertFalse(report["projects"][0]["has_legacy"])

    def test_each_annotator_sees_only_their_own_work(self):
        clock = FrozenClock()
        self._work(clock, self.task, 60, token="a")
        assign_task_to_annotator(self.task, annotator=self.other)
        self.task.refresh_from_db()
        clock.tick(10)
        self._work(clock, self.task, 30, actor=self.other, token="o")

        mine = self._report(clock, self.annotator)
        theirs = self._report(clock, self.other)
        self.assertEqual(mine["seconds"], 60)
        self.assertEqual(theirs["seconds"], 30)

    def test_volume_time_is_the_union_across_annotators_not_per_person(self):
        clock = FrozenClock()
        self._work(clock, self.task, 60, token="a")
        assign_task_to_annotator(self.task, annotator=self.other)
        self.task.refresh_from_db()
        clock.tick(10)
        self._work(clock, self.task, 30, actor=self.other, token="o")
        with frozen(clock):
            self.assertEqual(timing.volume_time(self.volume)["seconds"], 90)

    def test_the_report_endpoint_is_manager_only_except_for_yourself(self):
        url = f"/api/people/{self.annotator.username}/time/"
        self.assertEqual(self.client_for(self.manager).get(url).status_code, 200)
        self.assertEqual(self.client_for(self.annotator).get(url).status_code, 200)
        self.assertEqual(self.client_for(self.other).get(url).status_code, 403)
        self.assertEqual(self.client_for(self.requester).get(url).status_code, 403)
        self.assertIn(APIClient().get(url).status_code, (401, 403))

    def test_format_duration_is_compact_at_every_scale(self):
        cases = {
            None: "-", 0: "0m", 59: "0m", 60: "1m", 2220: "37m",
            3600: "1h", 8040: "2h 14m", 86400: "1d", 273600: "3d 4h",
        }
        for seconds, expected in cases.items():
            self.assertEqual(timing.format_duration(seconds), expected, seconds)


# ---------------------------------------------------------------------------
# 26-27: timing never breaks annotation, and never becomes N+1
# ---------------------------------------------------------------------------


class ResilienceTests(TimingFixture, TestCase):
    def setUp(self):
        self.build()

    def test_a_timing_failure_does_not_break_a_task_read(self):
        with mock.patch(
            "annotation.timing.task_time_map", side_effect=RuntimeError("boom")
        ), mock.patch(
            "annotation.timing.task_time", side_effect=RuntimeError("boom")
        ):
            response = self.client_for(self.annotator).get(
                f"/api/tasks/{self.task.id}/"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["annotation_time"]["display"], "-")

    def test_submit_still_runs_when_closing_the_interval_explodes(self):
        """The Submit path calls timing through ``safely``, so it cannot inherit
        timing's failure modes. Asserted on the wrapper the service uses, with
        the real exploding function underneath."""
        with mock.patch(
            "annotation.timing.stop_task_timing", side_effect=RuntimeError("boom")
        ):
            self.assertIsNone(
                timing.safely(timing.stop_task_timing, self.task)
            )

    def test_reassignment_still_completes_when_timing_explodes(self):
        clock = FrozenClock()
        self.start(clock)
        with mock.patch(
            "annotation.timing.stop_task_timing", side_effect=RuntimeError("boom")
        ), frozen(clock):
            assign_task_to_annotator(self.task, annotator=self.other)
        self.task.refresh_from_db()
        self.assertEqual(self.task.assigned_to_id, self.other.id)

    def test_safely_swallows_and_reports_nothing_upward(self):
        def explode():
            raise RuntimeError("boom")

        self.assertIsNone(timing.safely(explode))

    def test_a_task_list_costs_one_timing_query_not_one_per_task(self):
        volumes = [self.make_volume(f"bulk-{i}") for i in range(12)]
        tasks = [self.make_task(v, assigned_to=self.annotator) for v in volumes]
        clock = FrozenClock()
        for index, task in enumerate(tasks[:4]):
            session = self.start(clock, task=task, token=f"t{index}")
            clock.tick(30)
            self.beat(clock, session, task=task)
            self.stop(clock, session)

        with frozen(clock):
            with self.assertNumQueries(1):
                mapped = timing.task_time_map([self.task, *tasks])
        self.assertEqual(len(mapped), len(tasks) + 1)
        self.assertEqual(mapped[tasks[0].id]["seconds"], 30)
        self.assertEqual(mapped[tasks[-1].id]["seconds"], 0)

    def test_the_drill_down_report_does_not_scale_queries_with_volumes(self):
        for index in range(15):
            volume = self.make_volume(f"many-{index}")
            self.make_task(volume, assigned_to=self.annotator)
        clock = FrozenClock()
        with frozen(clock):
            with self.assertNumQueries(2):
                report = timing.annotator_time_report(self.annotator)
        self.assertGreaterEqual(len(report["projects"][0]["datasets"]), 1)

    def test_the_task_list_endpoint_batches_its_timing_query(self):
        """The guard that matters: the *serializer* must batch, not just the helper.

        Asserted by counting interval queries during a real list render — a
        per-row ``task_time`` would show up here as one per task even though
        ``task_time_map`` is batched in isolation.
        """
        volumes = [self.make_volume(f"api-{i}") for i in range(8)]
        for volume in volumes:
            self.make_task(volume, assigned_to=self.annotator)

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        client = self.client_for(self.manager)
        with CaptureQueriesContext(connection) as captured:
            response = client.get(f"/api/projects/{self.project.id}/tasks/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 9)
        interval_queries = [
            q for q in captured.captured_queries
            if "annotation_workinterval" in q["sql"]
        ]
        self.assertEqual(len(interval_queries), 1, interval_queries)
        self.assertTrue(
            all(row["annotation_time"]["display"] == "0m" for row in response.json())
        )

    def test_the_plan_rows_endpoint_batches_its_timing_query(self):
        volumes = [self.make_volume(f"plan-{i}") for i in range(8)]
        for volume in volumes:
            self.make_task(volume, assigned_to=self.annotator)

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        client = self.client_for(self.manager)
        with CaptureQueriesContext(connection) as captured:
            response = client.post(
                f"/api/projects/{self.project.id}/assign-plan/rows/", {},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        interval_queries = [
            q for q in captured.captured_queries
            if "annotation_workinterval" in q["sql"]
        ]
        self.assertEqual(len(interval_queries), 1, interval_queries)
        self.assertTrue(
            all("annotation_time" in row for row in response.json()["entries"])
        )

    def test_a_legacy_only_task_list_issues_no_interval_query_at_all(self):
        legacy = self.make_volume("legacy-only", eligible=False)
        task = self.make_task(legacy, assigned_to=self.annotator)
        with self.assertNumQueries(0):
            mapped = timing.task_time_map([task])
        self.assertEqual(mapped[task.id]["display"], "-")
