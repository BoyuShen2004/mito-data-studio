"""Phase 7 — annotation operation log and work sessions.

Concurrency uses TransactionTestCase with real connections and skips off
PostgreSQL: sequence allocation depends on row locking and a unique constraint,
neither of which SQLite would exercise meaningfully.
"""

from __future__ import annotations

import threading
import unittest
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import AnnotatorProfile, Institution, UserProfile
from annotation.models import AnnotationOperation, AnnotationTask, WorkSession
from annotation.operations import (
    CURRENT_SCHEMA_VERSION,
    OperationError,
    VersionConflict,
    append_operation,
    current_version,
    history,
    latest_undoable,
    operations_enabled,
    redo,
    undo,
    verify_history,
)
from annotation.review_errors import ImmutableReviewError
from annotation.sessions import (
    SessionError,
    active_seconds_for,
    close_stale_sessions,
    credited_seconds,
    end_session,
    heartbeat,
    project_active_time,
    start_session,
    task_active_time,
)
from core.choices import TaskStatus, UserRole
from projects.models import Dataset, Project
from volumes.models import Volume

OPS_ON = override_settings(FEATURE_ANNOTATION_OPS=True)
OPS_OFF = override_settings(FEATURE_ANNOTATION_OPS=False)

requires_postgres = unittest.skipUnless(
    connection.vendor == "postgresql",
    "sequence allocation depends on PostgreSQL row locking",
)

K = AnnotationOperation.Kind


def make_user(name, role=UserRole.ANNOTATOR):
    u, _ = User.objects.get_or_create(username=name)
    u.set_password("pw-for-tests-1")
    u.save()
    UserProfile.objects.update_or_create(user=u, defaults={"role": role})
    if role == UserRole.ANNOTATOR:
        AnnotatorProfile.objects.update_or_create(
            user=u, defaults={"is_active_annotator": True, "max_active_tasks": 50}
        )
    # Re-fetch: `ensure_user_profile` creates a profile on insert and Django
    # caches that reverse one-to-one on the instance, so `u.profile.role` would
    # still read the signal's default rather than the role just written. That
    # cached value would make is_manager() answer False for a manager.
    return User.objects.get(pk=u.pk)


class OpsFixtureMixin:
    def build(self):
        self.org, _ = Institution.objects.get_or_create(name="Ops Org")
        self.manager = make_user("op-mgr", UserRole.MANAGER)
        self.annotator = make_user("op-ann")
        self.other = make_user("op-other")
        self.project = Project.objects.create(
            title="Ops", created_by=self.manager, manager_reviewed=True
        )
        self.dataset = Dataset.objects.create(project=self.project, name="ds")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="v", image_path="a.tif"
        )
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume,
            z_start=0, z_end=1, y_end=64, x_end=64,
            assigned_to=self.annotator, status=TaskStatus.ASSIGNED,
        )
        return self.task

    def op(self, *, actor=None, kind=K.PAINT_SLICE, **kw):
        return append_operation(
            task=self.task, actor=actor or self.annotator, kind=kind, **kw
        )


# ---------------------------------------------------------------------------
# Gating / backward compatibility
# ---------------------------------------------------------------------------


@OPS_OFF
class OperationsDisabledTests(OpsFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def test_disabled_by_default(self):
        self.assertFalse(operations_enabled())

    def test_append_refuses(self):
        with self.assertRaises(OperationError) as ctx:
            self.op()
        self.assertEqual(ctx.exception.reason, "disabled")

    def test_nothing_is_recorded(self):
        with self.assertRaises(OperationError):
            self.op()
        self.assertEqual(AnnotationOperation.objects.count(), 0)

    def test_undo_refuses(self):
        with self.assertRaises(OperationError):
            undo(self.task, actor=self.annotator)

    def test_session_start_refuses(self):
        with self.assertRaises(SessionError):
            start_session(task=self.task, actor=self.annotator)

    def test_legacy_task_reports_version_zero(self):
        """A task predating the log is valid, not broken."""
        self.assertEqual(current_version(self.task), 0)
        self.assertEqual(history(self.task), [])


# ---------------------------------------------------------------------------
# Append / ordering
# ---------------------------------------------------------------------------


@OPS_ON
class AppendTests(OpsFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def test_first_operation_is_seq_one(self):
        self.assertEqual(self.op().seq, 1)

    def test_sequence_is_dense_and_monotonic(self):
        seqs = [self.op().seq for _ in range(5)]
        self.assertEqual(seqs, [1, 2, 3, 4, 5])

    def test_current_version_tracks_the_log(self):
        self.op(); self.op()
        self.assertEqual(current_version(self.task), 2)

    def test_operation_has_a_uuid(self):
        self.assertIsNotNone(self.op().id)

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(OperationError):
            self.op(kind="teleport")

    def test_payload_is_digested(self):
        op = self.op(payload={"axis": "z", "index": 4})
        self.assertTrue(op.payload_digest)
        self.assertEqual(len(op.payload_digest), 64)

    def test_unsupported_schema_version_is_rejected(self):
        with self.assertRaises(OperationError) as ctx:
            self.op(schema_version=CURRENT_SCHEMA_VERSION + 99)
        self.assertEqual(ctx.exception.reason, "unsupported_schema")

    def test_oversized_payload_is_rejected(self):
        with self.assertRaises(OperationError) as ctx:
            self.op(payload={"runs": "x" * 40000})
        self.assertEqual(ctx.exception.reason, "payload_too_large")

    @override_settings(MITO_OP_PAYLOAD_MAX_BYTES=64)
    def test_payload_limit_is_configurable(self):
        with self.assertRaises(OperationError):
            self.op(payload={"a": "y" * 200})

    def test_non_serialisable_payload_is_rejected(self):
        with self.assertRaises(OperationError):
            self.op(payload={"bad": {1, 2, 3}})

    def test_non_dict_payload_is_rejected(self):
        with self.assertRaises(OperationError):
            self.op(payload=["not", "a", "dict"])

    def test_payload_ref_carries_voxel_location(self):
        """Voxels live on disk; the row references them."""
        op = self.op(payload_ref="working/task_1/delta_0001.tif")
        self.assertEqual(op.payload_ref, "working/task_1/delta_0001.tif")

    def test_client_ts_is_recorded_but_not_authoritative(self):
        bogus = timezone.now() - timedelta(days=365)
        op = self.op(client_ts=bogus)
        self.assertEqual(op.client_ts, bogus)
        # server_ts is the real clock and is not influenced by the client.
        self.assertGreater(op.server_ts, bogus)

    def test_operation_is_immutable(self):
        op = self.op()
        op.kind = K.ERASE_SLICE
        with self.assertRaises(ImmutableReviewError):
            op.save()

    def test_queryset_update_bypasses_immutability(self):
        """Known gap, same as Phase 5. Pinned so nobody assumes otherwise."""
        op = self.op()
        AnnotationOperation.objects.filter(pk=op.pk).update(kind=K.ERASE_SLICE)
        self.assertEqual(
            AnnotationOperation.objects.get(pk=op.pk).kind, K.ERASE_SLICE
        )


# ---------------------------------------------------------------------------
# Idempotency and version conflicts
# ---------------------------------------------------------------------------


@OPS_ON
class IdempotencyTests(OpsFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def test_replaying_a_key_returns_the_original(self):
        a = self.op(idempotency_key="k1")
        b = self.op(idempotency_key="k1")
        self.assertEqual(a.id, b.id)
        self.assertEqual(AnnotationOperation.objects.count(), 1)

    def test_replay_does_not_advance_the_sequence(self):
        self.op(idempotency_key="k1")
        self.op(idempotency_key="k1")
        self.assertEqual(current_version(self.task), 1)

    def test_same_key_different_payload_still_returns_the_original(self):
        """A retry is a retry. The first write wins; the log is append-only."""
        a = self.op(idempotency_key="k", payload={"v": 1})
        b = self.op(idempotency_key="k", payload={"v": 2})
        self.assertEqual(a.id, b.id)
        self.assertEqual(b.payload, {"v": 1})

    def test_same_key_from_different_users_is_independent(self):
        a = self.op(actor=self.annotator, idempotency_key="shared")
        b = self.op(actor=self.other, idempotency_key="shared")
        self.assertNotEqual(a.id, b.id)

    def test_no_key_means_no_idempotency(self):
        self.assertNotEqual(self.op().id, self.op().id)

    def test_expected_version_matching_succeeds(self):
        self.op()
        self.assertEqual(self.op(expected_version=1).seq, 2)

    def test_stale_expected_version_conflicts(self):
        self.op(); self.op()
        with self.assertRaises(VersionConflict) as ctx:
            self.op(expected_version=1)
        self.assertEqual(ctx.exception.current_version, 2)

    def test_conflict_carries_the_missed_operations(self):
        """A conflict the client cannot act on forces a full reload."""
        first = self.op()
        second = self.op()
        with self.assertRaises(VersionConflict) as ctx:
            self.op(expected_version=0)
        missed = [o.seq for o in ctx.exception.missed]
        self.assertEqual(missed, [first.seq, second.seq])

    def test_conflict_writes_nothing(self):
        self.op()
        with self.assertRaises(VersionConflict):
            self.op(expected_version=0)
        self.assertEqual(current_version(self.task), 1)


# ---------------------------------------------------------------------------
# History and integrity
# ---------------------------------------------------------------------------


@OPS_ON
class HistoryTests(OpsFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def test_history_is_ordered_oldest_first(self):
        ops = [self.op() for _ in range(4)]
        self.assertEqual([o.seq for o in history(self.task)], [1, 2, 3, 4])
        self.assertEqual(history(self.task)[0].id, ops[0].id)

    def test_history_is_bounded(self):
        for _ in range(10):
            self.op()
        self.assertEqual(len(history(self.task, limit=3)), 3)

    def test_history_limit_is_hard_capped(self):
        from annotation.operations import MAX_HISTORY_LIMIT

        for _ in range(3):
            self.op()
        self.assertLessEqual(len(history(self.task, limit=10**6)), MAX_HISTORY_LIMIT)

    def test_after_seq_returns_only_the_tail(self):
        for _ in range(5):
            self.op()
        self.assertEqual([o.seq for o in history(self.task, after_seq=3)], [4, 5])

    def test_verify_history_passes_on_a_clean_log(self):
        for _ in range(3):
            self.op(payload={"n": 1})
        report = verify_history(self.task)
        self.assertTrue(report["ok"])
        self.assertTrue(report["dense"])
        self.assertEqual(report["operations"], 3)

    def test_verify_detects_a_tampered_payload(self):
        """Corruption is detectable without replaying anything."""
        op = self.op(payload={"n": 1})
        AnnotationOperation.objects.filter(pk=op.pk).update(payload={"n": 999})
        report = verify_history(self.task)
        self.assertFalse(report["ok"])
        self.assertIn(str(op.id), report["digest_mismatches"])

    def test_verify_detects_a_gap(self):
        self.op(); second = self.op()
        AnnotationOperation.objects.filter(pk=second.pk).update(seq=99)
        self.assertFalse(verify_history(self.task)["dense"])

    def test_history_is_scoped_to_its_task(self):
        other = AnnotationTask.objects.create(
            project=self.project, volume=self.volume,
            z_start=5, z_end=6, y_end=64, x_end=64,
        )
        self.op()
        self.assertEqual(history(other), [])


# ---------------------------------------------------------------------------
# Undo / redo
# ---------------------------------------------------------------------------


@OPS_ON
class UndoRedoTests(OpsFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def test_undo_appends_rather_than_deletes(self):
        target = self.op()
        before = AnnotationOperation.objects.count()
        inverse = undo(self.task, actor=self.annotator)
        self.assertEqual(AnnotationOperation.objects.count(), before + 1)
        self.assertEqual(inverse.kind, K.UNDO)
        self.assertEqual(inverse.inverse_of_id, target.id)
        self.assertTrue(AnnotationOperation.objects.filter(pk=target.pk).exists())

    def test_undone_operation_is_marked(self):
        target = self.op()
        undo(self.task, actor=self.annotator)
        target.refresh_from_db()
        self.assertIsNotNone(target.undone_at)
        self.assertTrue(target.is_undone)

    def test_nothing_to_undo(self):
        with self.assertRaises(OperationError) as ctx:
            undo(self.task, actor=self.annotator)
        self.assertEqual(ctx.exception.reason, "nothing_to_undo")

    def test_repeated_undo_walks_back(self):
        a = self.op(); b = self.op()
        undo(self.task, actor=self.annotator)
        undo(self.task, actor=self.annotator)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertIsNotNone(a.undone_at)
        self.assertIsNotNone(b.undone_at)

    def test_redo_restores(self):
        target = self.op()
        undo(self.task, actor=self.annotator)
        op = redo(self.task, actor=self.annotator)
        self.assertEqual(op.kind, K.REDO)
        target.refresh_from_db()
        self.assertIsNone(target.undone_at, "redo must revive the operation")

    def test_nothing_to_redo(self):
        self.op()
        with self.assertRaises(OperationError) as ctx:
            redo(self.task, actor=self.annotator)
        self.assertEqual(ctx.exception.reason, "nothing_to_redo")

    def test_new_edit_after_undo_appends(self):
        self.op()
        undo(self.task, actor=self.annotator)
        fresh = self.op()
        self.assertEqual(fresh.seq, 3)
        self.assertEqual(current_version(self.task), 3)

    def test_latest_undoable_skips_undo_operations(self):
        target = self.op()
        undo(self.task, actor=self.annotator)
        # The next undoable is not the UNDO row itself.
        self.assertIsNone(latest_undoable(self.task))
        self.assertTrue(target.pk)

    def test_cannot_undo_another_users_operation(self):
        self.op(actor=self.other)
        with self.assertRaises(OperationError) as ctx:
            undo(self.task, actor=self.annotator)
        self.assertEqual(ctx.exception.reason, "forbidden")

    def test_manager_may_undo_anyones_operation(self):
        self.op(actor=self.other)
        self.assertIsNotNone(undo(self.task, actor=self.manager))

    def test_undo_is_blocked_when_the_task_is_locked(self):
        """Phase 5 made annotation_locked the single gate on editing."""
        self.op()
        self.task.annotation_locked = True
        self.task.save(update_fields=["annotation_locked"])
        with self.assertRaises(OperationError) as ctx:
            undo(self.task, actor=self.annotator)
        self.assertEqual(ctx.exception.reason, "locked")

    def test_redo_is_blocked_when_the_task_is_locked(self):
        self.op()
        undo(self.task, actor=self.annotator)
        self.task.annotation_locked = True
        self.task.save(update_fields=["annotation_locked"])
        with self.assertRaises(OperationError) as ctx:
            redo(self.task, actor=self.annotator)
        self.assertEqual(ctx.exception.reason, "locked")

    def test_history_stays_append_only_across_undo_redo(self):
        self.op()
        undo(self.task, actor=self.annotator)
        redo(self.task, actor=self.annotator)
        self.assertEqual(AnnotationOperation.objects.filter(task=self.task).count(), 3)
        self.assertTrue(verify_history(self.task)["dense"])


# ---------------------------------------------------------------------------
# Active time
# ---------------------------------------------------------------------------


class CreditedSecondsTests(TestCase):
    """The policy itself — pure, no database."""

    def test_no_previous_heartbeat_credits_nothing(self):
        self.assertEqual(credited_seconds(None, timezone.now()), 0)

    def test_normal_interval_is_credited(self):
        now = timezone.now()
        self.assertEqual(credited_seconds(now - timedelta(seconds=30), now), 30)

    def test_interval_is_capped(self):
        now = timezone.now()
        self.assertEqual(
            credited_seconds(now - timedelta(seconds=5000), now,
                             max_interval=120, idle_timeout=10**6),
            120,
        )

    def test_idle_gap_credits_nothing(self):
        now = timezone.now()
        self.assertEqual(
            credited_seconds(now - timedelta(seconds=4000), now,
                             max_interval=120, idle_timeout=300),
            0,
        )

    def test_backwards_clock_credits_nothing_not_negative(self):
        now = timezone.now()
        self.assertEqual(credited_seconds(now + timedelta(seconds=60), now), 0)

    def test_zero_interval_credits_nothing(self):
        now = timezone.now()
        self.assertEqual(credited_seconds(now, now), 0)


@OPS_ON
class WorkSessionTests(OpsFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def test_start_session(self):
        s = start_session(task=self.task, actor=self.annotator)
        self.assertTrue(s.is_open)
        self.assertEqual(s.active_seconds, 0)

    def test_heartbeat_credits_time(self):
        s = start_session(task=self.task, actor=self.annotator)
        WorkSession.objects.filter(pk=s.pk).update(
            last_heartbeat_at=timezone.now() - timedelta(seconds=30)
        )
        s.refresh_from_db()
        s = heartbeat(s, actor=self.annotator)
        self.assertGreaterEqual(s.active_seconds, 29)
        self.assertLessEqual(s.active_seconds, 31)

    def test_duplicate_heartbeat_credits_almost_nothing(self):
        s = start_session(task=self.task, actor=self.annotator)
        s = heartbeat(s, actor=self.annotator)
        s = heartbeat(s, actor=self.annotator)
        self.assertLessEqual(s.active_seconds, 1)

    def test_delayed_heartbeat_is_capped(self):
        s = start_session(task=self.task, actor=self.annotator)
        WorkSession.objects.filter(pk=s.pk).update(
            last_heartbeat_at=timezone.now() - timedelta(seconds=200)
        )
        s.refresh_from_db()
        with override_settings(MITO_SESSION_MAX_HEARTBEAT_SECONDS=120,
                               MITO_SESSION_IDLE_TIMEOUT_SECONDS=600):
            s = heartbeat(s, actor=self.annotator)
        self.assertEqual(s.active_seconds, 120)

    def test_idle_gap_credits_nothing(self):
        s = start_session(task=self.task, actor=self.annotator)
        WorkSession.objects.filter(pk=s.pk).update(
            last_heartbeat_at=timezone.now() - timedelta(seconds=4000)
        )
        s.refresh_from_db()
        s = heartbeat(s, actor=self.annotator)
        self.assertEqual(s.active_seconds, 0)

    def test_client_timestamp_cannot_inflate_time(self):
        """A hostile or wrong clock must not be able to invent work."""
        s = start_session(task=self.task, actor=self.annotator)
        s = heartbeat(s, actor=self.annotator,
                      client_ts=timezone.now() + timedelta(days=30))
        self.assertLessEqual(s.active_seconds, 1)

    def test_cannot_heartbeat_someone_elses_session(self):
        s = start_session(task=self.task, actor=self.annotator)
        with self.assertRaises(SessionError) as ctx:
            heartbeat(s, actor=self.other)
        self.assertEqual(ctx.exception.reason, "forbidden")

    def test_end_session_credits_the_final_interval(self):
        s = start_session(task=self.task, actor=self.annotator)
        WorkSession.objects.filter(pk=s.pk).update(
            last_heartbeat_at=timezone.now() - timedelta(seconds=20)
        )
        s.refresh_from_db()
        s = end_session(s, actor=self.annotator)
        self.assertFalse(s.is_open)
        self.assertGreaterEqual(s.active_seconds, 19)

    def test_ending_twice_is_idempotent(self):
        s = start_session(task=self.task, actor=self.annotator)
        s = end_session(s, actor=self.annotator)
        seconds, ended = s.active_seconds, s.ended_at
        s = end_session(s, actor=self.annotator)
        self.assertEqual(s.active_seconds, seconds)
        self.assertEqual(s.ended_at, ended)

    def test_heartbeat_after_close_is_refused(self):
        s = start_session(task=self.task, actor=self.annotator)
        end_session(s, actor=self.annotator)
        with self.assertRaises(SessionError) as ctx:
            heartbeat(s, actor=self.annotator)
        self.assertEqual(ctx.exception.reason, "closed")

    def test_stale_session_is_swept_closed(self):
        """The browser-crash path: nothing after the last heartbeat counts."""
        s = start_session(task=self.task, actor=self.annotator)
        WorkSession.objects.filter(pk=s.pk).update(
            last_heartbeat_at=timezone.now() - timedelta(seconds=4000)
        )
        self.assertEqual(close_stale_sessions(), 1)
        s.refresh_from_db()
        self.assertFalse(s.is_open)
        self.assertEqual(s.active_seconds, 0)

    def test_live_session_is_not_swept(self):
        start_session(task=self.task, actor=self.annotator)
        self.assertEqual(close_stale_sessions(), 0)

    def test_active_seconds_never_negative(self):
        s = start_session(task=self.task, actor=self.annotator)
        heartbeat(s, actor=self.annotator)
        self.assertGreaterEqual(
            WorkSession.objects.get(pk=s.pk).active_seconds, 0
        )


@OPS_ON
class ActiveTimeAggregationTests(OpsFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def _session(self, actor, start_offset, end_offset, seconds):
        now = timezone.now()
        s = start_session(task=self.task, actor=actor)
        WorkSession.objects.filter(pk=s.pk).update(
            started_at=now - timedelta(seconds=start_offset),
            last_heartbeat_at=now - timedelta(seconds=end_offset),
            ended_at=now - timedelta(seconds=end_offset),
            active_seconds=seconds,
        )
        s.refresh_from_db()
        return s

    def test_no_sessions_reports_zero_and_unmeasured(self):
        summary = task_active_time(self.task)
        self.assertEqual(summary["active_seconds"], 0)
        self.assertFalse(summary["measured"])

    def test_single_session_totals(self):
        self._session(self.annotator, 600, 0, 600)
        self.assertGreaterEqual(task_active_time(self.task)["active_seconds"], 590)

    def test_overlapping_tabs_are_not_double_counted(self):
        """Two tabs open for the same hour is one hour, not two."""
        self._session(self.annotator, 3600, 0, 3600)
        self._session(self.annotator, 3600, 0, 3600)
        merged = active_seconds_for(actor=self.annotator, task=self.task)
        raw = active_seconds_for(actor=self.annotator, task=self.task,
                                 deduplicate_overlap=False)
        self.assertEqual(raw, 7200)
        self.assertLessEqual(merged, 3700, "overlap must be merged, not summed")

    def test_sequential_sessions_add_up(self):
        self._session(self.annotator, 7200, 3600, 3600)
        self._session(self.annotator, 3600, 0, 3600)
        merged = active_seconds_for(actor=self.annotator, task=self.task)
        self.assertGreaterEqual(merged, 7000)

    def test_two_users_are_counted_separately(self):
        self._session(self.annotator, 3600, 0, 3600)
        self._session(self.other, 3600, 0, 3600)
        self.assertGreaterEqual(active_seconds_for(task=self.task), 7000)

    def test_project_coverage_reports_measurement_gaps(self):
        """Legacy work has no sessions; that must be visible, not implied zero."""
        AnnotationTask.objects.create(
            project=self.project, volume=self.volume,
            z_start=9, z_end=10, y_end=64, x_end=64,
        )
        self._session(self.annotator, 600, 0, 600)
        summary = project_active_time(self.project)
        self.assertEqual(summary["tasks_total"], 2)
        self.assertEqual(summary["tasks_measured"], 1)
        self.assertEqual(summary["coverage"], 0.5)

    def test_utc_storage_makes_timezones_irrelevant(self):
        s = self._session(self.annotator, 600, 0, 600)
        self.assertEqual(s.started_at.tzinfo.utcoffset(s.started_at),
                         timedelta(0))


# ---------------------------------------------------------------------------
# Interaction with earlier phases
# ---------------------------------------------------------------------------


@OPS_ON
class PhaseInteractionTests(OpsFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def test_operations_do_not_require_the_task_hierarchy(self):
        """Phase map says Phase 7 depends on 0-1, not on Phase 2."""
        self.assertEqual(self.op().seq, 1)

    @override_settings(FEATURE_REVIEW_HISTORY=True)
    def test_review_transitions_create_no_operations(self):
        """Submission and review are distinct from edit operations."""
        import io
        import os
        import tempfile

        import numpy as np
        import tifffile
        from django.core.files.uploadedfile import SimpleUploadedFile

        from annotation.services import approve_submission, submit_annotation

        # Approving an upload installs it as the official label and re-seeds
        # the working draft from it, so both the registered image and the
        # submitted mask have to be readable volumes rather than placeholders.
        root = tempfile.mkdtemp()
        tifffile.imwrite(
            os.path.join(root, "a.tif"), np.zeros((1, 64, 64), dtype=np.uint8)
        )
        payload = io.BytesIO()
        tifffile.imwrite(payload, np.zeros((1, 64, 64), dtype=np.uint16))
        with override_settings(MITO_DATA_ROOT=root):
            s = submit_annotation(
                task=self.task, annotator=self.annotator,
                label_file=SimpleUploadedFile("a.tif", payload.getvalue()),
            )
            approve_submission(s, reviewer=self.manager)
        self.assertEqual(AnnotationOperation.objects.count(), 0)

    def test_operations_do_not_appear_in_the_audit_log(self):
        """AuditEvent is the permission log; edits must not drown it."""
        from accounts.models import AuditEvent

        before = AuditEvent.objects.count()
        for _ in range(5):
            self.op()
        self.assertEqual(AuditEvent.objects.count(), before)

    @override_settings(FEATURE_DASHBOARDS=True)
    def test_phase6_elapsed_fields_are_unchanged(self):
        """Active time must not silently redefine wall-clock elapsed."""
        from core.statistics import elapsed_durations, project_dashboard

        self.op()
        start_session(task=self.task, actor=self.annotator)
        d = project_dashboard(self.project)
        self.assertIn("mean_elapsed_to_submit_seconds", d["elapsed"])
        self.assertNotIn("active_seconds", d["elapsed"])
        self.assertEqual(
            set(elapsed_durations(self.project)),
            {"mean_elapsed_to_submit_seconds",
             "mean_elapsed_to_approve_seconds",
             "mean_elapsed_cycle_seconds"},
        )


# ---------------------------------------------------------------------------
# Concurrency — PostgreSQL only
# ---------------------------------------------------------------------------


@requires_postgres
@OPS_ON
class ConcurrentAppendTests(OpsFixtureMixin, TransactionTestCase):
    TIMEOUT = 30

    def setUp(self):
        self.build()

    def _race(self, n, fn):
        errors, lock = [], threading.Lock()
        barrier = threading.Barrier(n, timeout=self.TIMEOUT)

        def worker(i):
            try:
                barrier.wait()
                fn(i)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=self.TIMEOUT)
        self.assertEqual([t for t in threads if t.is_alive()], [], "append deadlock")
        return errors

    def test_concurrent_appends_produce_a_dense_sequence(self):
        errors = self._race(
            8, lambda i: append_operation(
                task=self.task, actor=self.annotator, kind=K.PAINT_SLICE,
                payload={"worker": i},
            )
        )
        self.assertEqual(errors, [])
        seqs = sorted(
            AnnotationOperation.objects.filter(task=self.task)
            .values_list("seq", flat=True)
        )
        self.assertEqual(seqs, list(range(1, 9)), "sequence must be dense")

    def test_concurrent_appends_from_two_users(self):
        users = [self.annotator, self.other]
        errors = self._race(
            6, lambda i: append_operation(
                task=self.task, actor=users[i % 2], kind=K.PAINT_SLICE,
            )
        )
        self.assertEqual(errors, [])
        self.assertEqual(current_version(self.task), 6)

    def test_concurrent_replay_of_one_key_appends_once(self):
        errors = self._race(
            5, lambda i: append_operation(
                task=self.task, actor=self.annotator, kind=K.PAINT_SLICE,
                idempotency_key="same",
            )
        )
        self.assertEqual(errors, [])
        self.assertEqual(
            AnnotationOperation.objects.filter(task=self.task).count(), 1
        )

    def test_concurrent_heartbeats_do_not_double_credit(self):
        session = start_session(task=self.task, actor=self.annotator)
        WorkSession.objects.filter(pk=session.pk).update(
            last_heartbeat_at=timezone.now() - timedelta(seconds=60)
        )
        errors = self._race(
            4, lambda i: heartbeat(
                WorkSession.objects.get(pk=session.pk), actor=self.annotator
            )
        )
        self.assertEqual(errors, [])
        session.refresh_from_db()
        # Only the first heartbeat sees a 60s gap; the rest see ~0.
        self.assertLessEqual(
            session.active_seconds, 65,
            "concurrent heartbeats must not each credit the full interval",
        )
