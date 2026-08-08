"""Phase 5 — review loop hardening.

Three properties, per ADR-003: append-only submission history, immutable review
records, and an explicit task-status transition table.

The phase gate is **parity with current UX**, so a large share of these tests
assert that nothing changed rather than that something did — with the flag off,
behaviour must be byte-identical to Phase 4.

Concurrency uses TransactionTestCase with real connections and skips off
PostgreSQL; SQLite would validate nothing about row locking.
"""

from __future__ import annotations

import io
import os
import tempfile
import threading
import unittest

import numpy as np
import tifffile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import AnnotatorProfile, Institution, Team, TeamMembership, UserProfile
from accounts.teams import grant_project_team
from annotation.models import AnnotationSubmission, AnnotationTask, ReviewRecord
from annotation.review_errors import ImmutableReviewError
from annotation.services import (
    approve_submission,
    can_annotate_task,
    can_submit_task,
    current_submission,
    reject_submission,
    request_revision,
    submission_history,
    submit_annotation,
)
from annotation.transitions import (
    ALLOWED_TRANSITIONS,
    IllegalTransition,
    assert_transition,
    is_legal,
    legal_targets,
)
from core.choices import ReviewDecision, TaskStatus, UserRole
from projects.models import Dataset, Project
from volumes.models import Volume

_TMP = tempfile.mkdtemp(prefix="mito_phase5_")

HISTORY_ON = override_settings(FEATURE_REVIEW_HISTORY=True, MITO_DATA_ROOT=_TMP)
HISTORY_OFF = override_settings(FEATURE_REVIEW_HISTORY=False, MITO_DATA_ROOT=_TMP)

requires_postgres = unittest.skipUnless(
    connection.vendor == "postgresql",
    "row locking is a no-op outside PostgreSQL",
)


def make_user(username, role=UserRole.ANNOTATOR):
    u = User.objects.create_user(username, password="pw-for-tests-1")
    UserProfile.objects.update_or_create(user=u, defaults={"role": role})
    if role == UserRole.ANNOTATOR:
        AnnotatorProfile.objects.update_or_create(
            user=u, defaults={"is_active_annotator": True, "max_active_tasks": 5}
        )
    return u


class ReviewFixtureMixin:
    def build(self):
        self.manager = make_user("p5-mgr", UserRole.MANAGER)
        self.annotator = make_user("p5-ann")
        self.project = Project.objects.create(
            title="P5", created_by=self.manager, manager_reviewed=True
        )
        organization = Institution.objects.create(name=f"P5 org {self.project.id}")
        team = Team.objects.create(organization=organization, name="P5 team")
        TeamMembership.objects.create(team=team, user=self.annotator)
        grant_project_team(self.project, team)
        self.dataset = Dataset.objects.create(project=self.project, name="ds")
        image_path = os.path.join(_TMP, "a.tif")
        tifffile.imwrite(image_path, np.zeros((1, 64, 64), dtype=np.uint8))
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="v", image_path="a.tif",
            shape_z=1, shape_y=64, shape_x=64,
        )
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume,
            z_start=0, z_end=1, y_end=64, x_end=64,
            assigned_to=self.annotator, status=TaskStatus.ASSIGNED,
        )

    def submit(self, name="m.tif", notes=""):
        payload = io.BytesIO()
        tifffile.imwrite(payload, np.zeros((1, 64, 64), dtype=np.uint16))
        return submit_annotation(
            task=self.task, annotator=self.annotator,
            label_file=SimpleUploadedFile(name, payload.getvalue()), notes=notes,
        )


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------


class TransitionTableTests(TestCase):
    """The table itself — pure logic, no database."""

    def test_every_status_has_a_rule(self):
        for status in TaskStatus.values:
            self.assertIn(status, ALLOWED_TRANSITIONS, f"no rule for {status}")

    def test_every_target_is_a_real_status(self):
        for src, targets in ALLOWED_TRANSITIONS.items():
            for t in targets:
                self.assertIn(t, TaskStatus.values, f"{src} -> {t} is not a status")

    def test_self_transition_is_always_legal(self):
        for status in TaskStatus.values:
            self.assertTrue(is_legal(status, status))

    def test_unknown_current_status_is_permitted(self):
        """A row predating the table must not become unwritable."""
        self.assertTrue(is_legal("some-legacy-status", TaskStatus.SUBMITTED))
        self.assertTrue(is_legal(None, TaskStatus.SUBMITTED))
        self.assertTrue(is_legal("", TaskStatus.SUBMITTED))

    def test_the_documented_review_cycle_is_legal(self):
        cycle = [
            (TaskStatus.UNASSIGNED, TaskStatus.ASSIGNED),
            (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS),
            (TaskStatus.IN_PROGRESS, TaskStatus.SUBMITTED),
            (TaskStatus.SUBMITTED, TaskStatus.REVISION_REQUESTED),
            (TaskStatus.REVISION_REQUESTED, TaskStatus.SUBMITTED),
            (TaskStatus.SUBMITTED, TaskStatus.REJECTED),
            (TaskStatus.REJECTED, TaskStatus.SUBMITTED),
            (TaskStatus.SUBMITTED, TaskStatus.APPROVED),
        ]
        for src, dst in cycle:
            self.assertTrue(is_legal(src, dst), f"{src} -> {dst} should be legal")

    def test_approved_may_reopen(self):
        """mito allows approve-with-further-annotation; approved is not terminal."""
        self.assertTrue(is_legal(TaskStatus.APPROVED, TaskStatus.IN_PROGRESS))
        self.assertTrue(is_legal(TaskStatus.APPROVED, TaskStatus.SUBMITTED))

    def test_illegal_jumps_are_rejected(self):
        for src, dst in [
            (TaskStatus.UNASSIGNED, TaskStatus.APPROVED),
            (TaskStatus.UNASSIGNED, TaskStatus.SUBMITTED),
            (TaskStatus.ASSIGNED, TaskStatus.APPROVED),
            (TaskStatus.IN_PROGRESS, TaskStatus.APPROVED),
            (TaskStatus.APPROVED, TaskStatus.REJECTED),
            (TaskStatus.REJECTED, TaskStatus.APPROVED),
        ]:
            self.assertFalse(is_legal(src, dst), f"{src} -> {dst} should be illegal")

    def test_legal_targets_includes_self(self):
        self.assertIn(
            TaskStatus.SUBMITTED, legal_targets(TaskStatus.SUBMITTED)
        )

    @override_settings(FEATURE_REVIEW_HISTORY=False)
    def test_illegal_transition_is_permitted_when_flag_off(self):
        """Shipped inert: deploying the table cannot break existing data."""
        assert_transition(TaskStatus.UNASSIGNED, TaskStatus.APPROVED)

    @override_settings(FEATURE_REVIEW_HISTORY=True)
    def test_illegal_transition_raises_when_flag_on(self):
        with self.assertRaises(IllegalTransition) as ctx:
            assert_transition(TaskStatus.UNASSIGNED, TaskStatus.APPROVED)
        self.assertEqual(ctx.exception.current, TaskStatus.UNASSIGNED)
        self.assertEqual(ctx.exception.target, TaskStatus.APPROVED)

    @override_settings(FEATURE_REVIEW_HISTORY=True)
    def test_legal_transition_never_raises(self):
        assert_transition(TaskStatus.SUBMITTED, TaskStatus.APPROVED)


# ---------------------------------------------------------------------------
# Immutable reviews
# ---------------------------------------------------------------------------


@HISTORY_OFF
class ImmutableReviewTests(ReviewFixtureMixin, TestCase):
    """Immutability is NOT flag-gated — a decision is never editable."""

    def setUp(self):
        self.build()
        self.submission = self.submit()
        self.review = approve_submission(
            self.submission, reviewer=self.manager, comments="ok"
        )

    def test_review_is_created(self):
        self.assertIsNotNone(self.review.pk)
        self.assertEqual(self.review.decision, ReviewDecision.APPROVED)

    def test_editing_a_review_raises(self):
        self.review.comments = "changed my mind"
        with self.assertRaises(ImmutableReviewError):
            self.review.save()

    def test_editing_is_blocked_even_with_update_fields(self):
        self.review.decision = ReviewDecision.REJECTED
        with self.assertRaises(ImmutableReviewError):
            self.review.save(update_fields=["decision"])

    def test_refetched_review_is_also_immutable(self):
        fresh = ReviewRecord.objects.get(pk=self.review.pk)
        fresh.comments = "x"
        with self.assertRaises(ImmutableReviewError):
            fresh.save()

    def test_the_stored_row_is_unchanged_after_a_blocked_edit(self):
        self.review.comments = "tampered"
        with self.assertRaises(ImmutableReviewError):
            self.review.save()
        self.assertEqual(
            ReviewRecord.objects.get(pk=self.review.pk).comments, "ok"
        )

    def test_queryset_update_is_a_known_gap(self):
        """Documented in ADR-003: .update() bypasses save() and is NOT blocked.

        Pinned so nobody assumes protection that does not exist. If this ever
        starts failing, a trigger was added and the ADR needs revisiting.
        """
        ReviewRecord.objects.filter(pk=self.review.pk).update(comments="bypassed")
        self.assertEqual(
            ReviewRecord.objects.get(pk=self.review.pk).comments, "bypassed"
        )

    def test_deletion_still_works(self):
        """Immutability blocks edits, not deletes — dev reset depends on it."""
        pk = self.review.pk
        ReviewRecord.objects.filter(pk=pk).delete()
        self.assertFalse(ReviewRecord.objects.filter(pk=pk).exists())

    def test_a_second_review_is_appended_not_merged(self):
        AnnotationTask.objects.filter(pk=self.task.pk).update(annotation_locked=False)
        # The mixin's submit() uses self.task, which still carries the lock in
        # memory from the approve above; refresh or the submit gate rejects it.
        self.task.refresh_from_db()
        second = self.submit("m2.tif")
        reject_submission(second, reviewer=self.manager, comments="no")
        self.assertEqual(ReviewRecord.objects.filter(task=self.task).count(), 2)


# ---------------------------------------------------------------------------
# Append-only history — flag OFF compatibility profile
# ---------------------------------------------------------------------------


@HISTORY_OFF
class HistoryDisabledParityTests(ReviewFixtureMixin, TestCase):
    """Durable review history is now a product invariant, not flag-gated."""

    def setUp(self):
        self.build()

    def test_resubmitting_retains_and_voids_the_previous_submission(self):
        first = self.submit("a.tif")
        second = self.submit("b.tif")
        remaining = list(AnnotationSubmission.objects.filter(task=self.task))
        self.assertEqual({row.id for row in remaining}, {first.id, second.id})
        first.refresh_from_db()
        self.assertEqual(first.review_status, "voided")

    def test_submission_count_still_increments(self):
        self.submit("a.tif")
        self.submit("b.tif")
        self.task.refresh_from_db()
        self.assertEqual(self.task.submission_count, 2)

    def test_current_submission_returns_the_latest(self):
        self.submit("a.tif")
        second = self.submit("b.tif")
        self.assertEqual(current_submission(self.task).pk, second.pk)

    def test_supersedes_is_populated_when_flag_off(self):
        first = self.submit("a.tif")
        second = self.submit("b.tif")
        second.refresh_from_db()
        self.assertEqual(second.supersedes_id, first.id)

    def test_previous_is_marked_superseded_when_flag_off(self):
        self.submit("a.tif")
        self.submit("b.tif")
        self.assertEqual(
            AnnotationSubmission.objects.filter(
                task=self.task, superseded_at__isnull=False
            ).count(), 1,
        )

    def test_review_still_locks_on_approve(self):
        s = self.submit()
        approve_submission(s, reviewer=self.manager)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.APPROVED)
        self.assertTrue(self.task.annotation_locked)

    def test_approve_with_further_annotation_leaves_unlocked(self):
        s = self.submit()
        approve_submission(
            s, reviewer=self.manager, allow_further_annotation=True
        )
        self.task.refresh_from_db()
        self.assertFalse(self.task.annotation_locked)

    def test_reject_reopens(self):
        s = self.submit()
        reject_submission(s, reviewer=self.manager)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.REJECTED)
        self.assertFalse(self.task.annotation_locked)

    def test_revision_reopens(self):
        s = self.submit()
        request_revision(s, reviewer=self.manager)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.REVISION_REQUESTED)
        self.assertFalse(self.task.annotation_locked)

    def test_permissions_are_unchanged(self):
        """The parity gate: submit/annotate gating keys off the lock alone."""
        self.assertTrue(can_submit_task(self.annotator, self.task))
        self.assertTrue(can_annotate_task(self.annotator, self.task))
        s = self.submit()
        approve_submission(s, reviewer=self.manager)
        self.task.refresh_from_db()
        self.assertFalse(can_submit_task(self.annotator, self.task))
        self.assertFalse(can_annotate_task(self.annotator, self.task))


# ---------------------------------------------------------------------------
# Append-only history — flag ON
# ---------------------------------------------------------------------------


@HISTORY_ON
class HistoryEnabledTests(ReviewFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def test_previous_submission_is_retained(self):
        first = self.submit("a.tif")
        self.submit("b.tif")
        self.assertTrue(AnnotationSubmission.objects.filter(pk=first.pk).exists())

    def test_previous_submission_is_marked_superseded(self):
        first = self.submit("a.tif")
        self.submit("b.tif")
        first.refresh_from_db()
        self.assertIsNotNone(first.superseded_at)
        self.assertFalse(first.is_current)

    def test_newest_submission_is_current(self):
        self.submit("a.tif")
        second = self.submit("b.tif")
        second.refresh_from_db()
        self.assertTrue(second.is_current)
        self.assertEqual(current_submission(self.task).pk, second.pk)

    def test_chain_links_rounds(self):
        first = self.submit("a.tif")
        second = self.submit("b.tif")
        second.refresh_from_db()
        self.assertEqual(second.supersedes_id, first.pk)

    def test_round_number_counts_the_chain(self):
        self.submit("a.tif")
        self.submit("b.tif")
        third = self.submit("c.tif")
        third.refresh_from_db()
        self.assertEqual(third.round_number, 3)

    def test_history_is_ordered_newest_first(self):
        a = self.submit("a.tif")
        b = self.submit("b.tif")
        c = self.submit("c.tif")
        got = [s.pk for s in submission_history(self.task)]
        self.assertEqual(got, [c.pk, b.pk, a.pk])

    def test_history_is_bounded_by_limit(self):
        for i in range(5):
            self.submit(f"{i}.tif")
        self.assertEqual(len(list(submission_history(self.task, limit=2))), 2)

    def test_retained_upload_keeps_its_file(self):
        """A history row pointing at a deleted file would be worse than none."""
        first = self.submit("a.tif")
        self.submit("b.tif")
        first.refresh_from_db()
        self.assertTrue(first.label_file)
        self.assertTrue(first.label_file.storage.exists(first.label_file.name))

    def test_exactly_one_current_submission(self):
        for i in range(4):
            self.submit(f"{i}.tif")
        self.assertEqual(
            AnnotationSubmission.objects.filter(
                task=self.task, superseded_at__isnull=True
            ).count(), 1,
        )

    def test_superseded_timestamp_is_stable(self):
        """Idempotent: replaying must not rewrite when a round ended."""
        first = self.submit("a.tif")
        self.submit("b.tif")
        first.refresh_from_db()
        stamp = first.superseded_at
        self.submit("c.tif")
        first.refresh_from_db()
        self.assertEqual(first.superseded_at, stamp)

    def test_reviews_survive_and_point_at_their_submission(self):
        first = self.submit("a.tif")
        review = request_revision(first, reviewer=self.manager, comments="redo")
        self.submit("b.tif")
        review.refresh_from_db()
        self.assertEqual(review.submission_id, first.pk)
        self.assertEqual(review.task_id, self.task.pk)

    def test_full_review_round_trip(self):
        first = self.submit("a.tif")
        request_revision(first, reviewer=self.manager, comments="again")
        second = self.submit("b.tif")
        approve_submission(second, reviewer=self.manager, comments="good")

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.APPROVED)
        self.assertEqual(self.task.submission_count, 2)
        self.assertEqual(AnnotationSubmission.objects.filter(task=self.task).count(), 2)
        self.assertEqual(ReviewRecord.objects.filter(task=self.task).count(), 2)

    def test_permissions_are_unchanged_with_history_on(self):
        self.assertTrue(can_submit_task(self.annotator, self.task))
        s = self.submit()
        approve_submission(s, reviewer=self.manager)
        self.task.refresh_from_db()
        self.assertFalse(can_submit_task(self.annotator, self.task))

    def test_empty_task_has_no_current_submission(self):
        self.assertIsNone(current_submission(self.task))
        self.assertEqual(list(submission_history(self.task)), [])


# ---------------------------------------------------------------------------
# Transitions through the real services
# ---------------------------------------------------------------------------


@HISTORY_ON
class ServiceTransitionEnforcementTests(ReviewFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def test_submit_from_assigned_is_legal(self):
        self.submit()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.SUBMITTED)

    def test_approve_from_submitted_is_legal(self):
        s = self.submit()
        approve_submission(s, reviewer=self.manager)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.APPROVED)

    def test_cannot_approve_a_task_that_was_never_submitted(self):
        """The illegal jump the table exists to stop."""
        s = self.submit()
        # Force the task back to an un-submitted state behind the service's back.
        AnnotationTask.objects.filter(pk=self.task.pk).update(
            status=TaskStatus.UNASSIGNED
        )
        with self.assertRaises(IllegalTransition):
            approve_submission(s, reviewer=self.manager)

    def test_a_blocked_transition_leaves_the_task_untouched(self):
        s = self.submit()
        AnnotationTask.objects.filter(pk=self.task.pk).update(
            status=TaskStatus.UNASSIGNED
        )
        with self.assertRaises(IllegalTransition):
            approve_submission(s, reviewer=self.manager)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.UNASSIGNED)
        self.assertIsNone(self.task.approved_at)

    def test_resubmit_after_revision_is_legal(self):
        first = self.submit("a.tif")
        request_revision(first, reviewer=self.manager)
        self.submit("b.tif")
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.SUBMITTED)

    def test_resubmit_after_reject_is_legal(self):
        first = self.submit("a.tif")
        reject_submission(first, reviewer=self.manager)
        self.submit("b.tif")
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.SUBMITTED)


# ---------------------------------------------------------------------------
# Interaction with Phases 2-4
# ---------------------------------------------------------------------------


@override_settings(
    FEATURE_REVIEW_HISTORY=True, FEATURE_AUTO_FILL_SCHEDULER=True,
    FEATURE_TEAMS=False, MITO_DATA_ROOT=_TMP,
)
class PhaseInteractionTests(ReviewFixtureMixin, TestCase):
    def setUp(self):
        self.build()
        self.task.assigned_to = None
        self.task.status = TaskStatus.UNASSIGNED
        self.task.assigned_at = None
        self.task.save(update_fields=["assigned_to", "status", "assigned_at"])

    def test_scheduler_assigned_task_reviews_normally(self):
        from annotation.scheduler import run_auto_fill

        self.annotator.last_login = timezone.now()
        self.annotator.save(update_fields=["last_login"])
        run_auto_fill()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.ASSIGNED)
        s = self.submit()
        request_revision(s, reviewer=self.manager)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.REVISION_REQUESTED)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@HISTORY_ON
class ReviewAuditTests(ReviewFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def test_review_writes_an_audit_event(self):
        from accounts.models import AuditEvent
        from core.choices import AuditVerb

        s = self.submit()
        approve_submission(s, reviewer=self.manager, comments="ok")
        events = AuditEvent.objects.filter(verb=AuditVerb.REVIEW_RECORDED)
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().actor_id, self.manager.id)

    def test_each_verdict_is_audited(self):
        from accounts.models import AuditEvent
        from core.choices import AuditVerb

        a = self.submit("a.tif")
        reject_submission(a, reviewer=self.manager)
        b = self.submit("b.tif")
        request_revision(b, reviewer=self.manager)
        c = self.submit("c.tif")
        approve_submission(c, reviewer=self.manager)
        self.assertEqual(
            AuditEvent.objects.filter(verb=AuditVerb.REVIEW_RECORDED).count(), 3
        )

    def test_audit_records_the_decision(self):
        from accounts.models import AuditEvent
        from core.choices import AuditVerb

        s = self.submit()
        reject_submission(s, reviewer=self.manager, comments="no")
        ev = AuditEvent.objects.filter(verb=AuditVerb.REVIEW_RECORDED).first()
        self.assertEqual(ev.metadata.get("decision"), ReviewDecision.REJECTED)


# ---------------------------------------------------------------------------
# Concurrency — PostgreSQL only
# ---------------------------------------------------------------------------


@requires_postgres
@HISTORY_ON
class ConcurrentSubmitTests(ReviewFixtureMixin, TransactionTestCase):
    """Racing resubmits must not leave two current submissions."""

    TIMEOUT = 30

    def setUp(self):
        self.build()

    def test_concurrent_resubmits_leave_one_current(self):
        self.submit("seed.tif")
        errors, lock = [], threading.Lock()
        barrier = threading.Barrier(4, timeout=self.TIMEOUT)

        def worker(i):
            try:
                barrier.wait()
                submit_annotation(
                    task=self.task, annotator=self.annotator,
                    label_file=SimpleUploadedFile(f"r{i}.tif", b"x"), notes="",
                )
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=self.TIMEOUT)
        self.assertEqual([t for t in threads if t.is_alive()], [], "submit deadlock")
        self.assertEqual(errors, [])

        current = AnnotationSubmission.objects.filter(
            task=self.task, superseded_at__isnull=True
        )
        self.assertEqual(
            current.count(), 1,
            f"expected exactly one current submission, got {current.count()}",
        )

    def test_no_submission_is_lost_under_contention(self):
        errors, lock = [], threading.Lock()
        barrier = threading.Barrier(5, timeout=self.TIMEOUT)

        def worker(i):
            try:
                barrier.wait()
                submit_annotation(
                    task=self.task, annotator=self.annotator,
                    label_file=SimpleUploadedFile(f"c{i}.tif", b"x"), notes="",
                )
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=self.TIMEOUT)

        self.assertEqual(errors, [])
        # Append-only: every submission that succeeded is still there.
        self.assertEqual(
            AnnotationSubmission.objects.filter(task=self.task).count(), 5
        )
