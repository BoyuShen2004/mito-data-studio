"""Every notification verb has a real emission point.

The regression this guards against is specific and was real: five verbs were
defined in ``NotificationVerb``, documented as having emission points, and
written by nothing. A vocabulary entry that no code path produces is worse
than an absent one — it reads as a feature.
"""

from __future__ import annotations

import io
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import tifffile
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AnnotatorProfile, Notification, UserProfile
from accounts.notifications import project_audience
from annotation.models import AnnotationTask
from annotation.services import (
    add_hard_case_message,
    assign_task_to_annotator,
    create_hard_case,
    submit_annotation,
    withdraw_project_assignments,
)
from core.choices import (
    MilestoneMetric,
    NotificationVerb,
    TaskStatus,
    TaskType,
    UserRole,
)
from projects.models import Dataset, Milestone, Project
from volumes.models import Volume

_TMP = tempfile.mkdtemp(prefix="mito_notify_")
SETTINGS = override_settings(MITO_DATA_ROOT=_TMP, MEDIA_ROOT=_TMP)


def make_user(username, role):
    user = User.objects.create_user(username=username, password="pw")
    UserProfile.objects.update_or_create(user=user, defaults={"role": role})
    AnnotatorProfile.objects.get_or_create(user=user)
    # Refetch: the profile is auto-created with the default annotator role and
    # cached on the instance, so `update_or_create` changes the row while this
    # object still reports "annotator" — and every `is_manager` check reading
    # it would silently test the wrong role. Same trap documented in
    # volumes/test_chunk_service.py.
    return User.objects.get(pk=user.pk)


def label_upload(name="s.tif"):
    buffer = io.BytesIO()
    tifffile.imwrite(buffer, np.zeros((2, 2, 2), dtype=np.uint16))
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/tiff")


@SETTINGS
class EmissionBaseTest(TestCase):
    def setUp(self):
        self.manager = make_user("mgr", UserRole.MANAGER)
        self.annotator = make_user("ann", UserRole.ANNOTATOR)
        self.other = make_user("ann2", UserRole.ANNOTATOR)
        self.project = Project.objects.create(title="P", created_by=self.manager)
        self.dataset = Dataset.objects.create(project=self.project, name="D")

        image = Path(settings.MITO_DATA_ROOT) / "notify-image.tif"
        image.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(str(image), np.zeros((2, 2, 2), dtype=np.uint16))
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="V",
            image_path="notify-image.tif", shape_z=2, shape_y=2, shape_x=2,
        )
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume, assigned_to=self.annotator,
            z_start=0, z_end=2, y_end=2, x_end=2,
            task_type=TaskType.MANUAL_ANNOTATION, status=TaskStatus.ASSIGNED,
        )

    def verbs_for(self, user):
        return set(
            Notification.objects.filter(recipient=user).values_list("verb", flat=True)
        )


class WithdrawalTests(EmissionBaseTest):
    def test_the_former_assignee_is_told_which_task_was_taken(self):
        withdraw_project_assignments(self.project, team_name="T")
        rows = Notification.objects.filter(
            recipient=self.annotator, verb=NotificationVerb.TASK_WITHDRAWN
        )
        self.assertEqual(rows.count(), 1)
        # Naming the volume is the point: "3 tasks withdrawn" hides the one
        # they were in the middle of.
        self.assertIn("V", rows.first().title)

    def test_the_recipient_survives_the_assignment_being_cleared(self):
        """The bug this guards: the withdrawal loop clears ``assigned_to`` on
        the very task objects it collects, so reading the assignee back off
        them afterwards yields None and notifies nobody."""
        withdraw_project_assignments(self.project, team_name="T")
        self.task.refresh_from_db()
        self.assertIsNone(self.task.assigned_to)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.annotator, verb=NotificationVerb.TASK_WITHDRAWN
            ).exists()
        )

    def test_one_notification_per_task_not_per_person(self):
        second_volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="V2",
            shape_z=2, shape_y=2, shape_x=2,
        )
        AnnotationTask.objects.create(
            project=self.project, volume=second_volume, assigned_to=self.annotator,
            z_start=0, z_end=2, y_end=2, x_end=2,
            task_type=TaskType.MANUAL_ANNOTATION, status=TaskStatus.ASSIGNED,
        )
        withdraw_project_assignments(self.project, team_name="T")
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.annotator, verb=NotificationVerb.TASK_WITHDRAWN
            ).count(),
            2,
        )


class HardCaseTests(EmissionBaseTest):
    def test_opening_a_case_tells_the_project_but_not_its_author(self):
        create_hard_case(task=self.task, user=self.annotator, label_id=4, note="odd")
        self.assertIn(NotificationVerb.HARD_CASE_OPENED, self.verbs_for(self.manager))
        self.assertNotIn(
            NotificationVerb.HARD_CASE_OPENED, self.verbs_for(self.annotator)
        )

    def test_a_reply_tells_the_project_but_not_its_author(self):
        case = create_hard_case(task=self.task, user=self.annotator, label_id=4)
        Notification.objects.all().delete()
        add_hard_case_message(case, user=self.manager, body="looks like a merge")
        self.assertIn(
            NotificationVerb.HARD_CASE_REPLIED, self.verbs_for(self.annotator)
        )
        self.assertNotIn(
            NotificationVerb.HARD_CASE_REPLIED, self.verbs_for(self.manager)
        )

    def test_an_unrelated_annotator_is_not_notified(self):
        """`self.other` holds no task on this project, so they are not part of
        its audience — matching ``is_project_member``."""
        create_hard_case(task=self.task, user=self.annotator, label_id=4)
        self.assertEqual(Notification.objects.filter(recipient=self.other).count(), 0)

    def test_the_audience_matches_is_project_member(self):
        from annotation.services import is_project_member

        audience = set(project_audience(self.project).values_list("pk", flat=True))
        for user in (self.manager, self.annotator, self.other):
            self.assertEqual(
                user.pk in audience,
                is_project_member(user, self.project),
                f"{user.username} disagrees",
            )


class DeadlineCommandTests(EmissionBaseTest):
    def test_an_approaching_deadline_notifies_the_assignee(self):
        self.task.deadline = timezone.localdate() + timedelta(days=1)
        self.task.save(update_fields=["deadline"])
        call_command("notify_deadlines", days=3)
        self.assertIn(
            NotificationVerb.DEADLINE_APPROACHING, self.verbs_for(self.annotator)
        )

    def test_running_twice_in_a_day_does_not_duplicate(self):
        """A cron that fires hourly must not produce twenty inbox rows."""
        self.task.deadline = timezone.localdate() + timedelta(days=1)
        self.task.save(update_fields=["deadline"])
        call_command("notify_deadlines", days=3)
        call_command("notify_deadlines", days=3)
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.annotator,
                verb=NotificationVerb.DEADLINE_APPROACHING,
            ).count(),
            1,
        )

    def test_approved_work_is_never_chased(self):
        self.task.deadline = timezone.localdate()
        self.task.status = TaskStatus.APPROVED
        self.task.save(update_fields=["deadline", "status"])
        call_command("notify_deadlines", days=3)
        self.assertEqual(Notification.objects.count(), 0)

    def test_rejected_work_is_still_chased(self):
        self.task.deadline = timezone.localdate()
        self.task.status = TaskStatus.REJECTED
        self.task.save(update_fields=["deadline", "status"])
        call_command("notify_deadlines", days=3)
        self.assertIn(
            NotificationVerb.DEADLINE_APPROACHING, self.verbs_for(self.annotator)
        )

    def test_a_dry_run_writes_nothing(self):
        self.task.deadline = timezone.localdate()
        self.task.save(update_fields=["deadline"])
        call_command("notify_deadlines", days=3, dry_run=True)
        self.assertEqual(Notification.objects.count(), 0)

    def test_an_at_risk_milestone_notifies_the_project(self):
        Milestone.objects.create(
            project=self.project, name="M",
            due_on=timezone.localdate() + timedelta(days=1),
            target_metric=MilestoneMetric.TASKS_APPROVED, target_value=5,
        )
        call_command("notify_deadlines", days=3)
        self.assertIn(NotificationVerb.MILESTONE_AT_RISK, self.verbs_for(self.manager))

    def test_a_met_milestone_is_not_flagged(self):
        """Its date being close is not a reason to warn about finished work."""
        Milestone.objects.create(
            project=self.project, name="M",
            due_on=timezone.localdate() + timedelta(days=1),
            target_metric=MilestoneMetric.TASKS_APPROVED, target_value=0,
        )
        call_command("notify_deadlines", days=3)
        self.assertNotIn(
            NotificationVerb.MILESTONE_AT_RISK, self.verbs_for(self.manager)
        )


class PruneTests(EmissionBaseTest):
    def _make(self, *, read, age_days):
        row = Notification.objects.create(
            recipient=self.annotator, verb=NotificationVerb.TASK_ASSIGNED,
            title="old",
        )
        when = timezone.now() - timedelta(days=age_days)
        Notification.objects.filter(pk=row.pk).update(
            created_at=when, read_at=when if read else None
        )
        return row

    def test_old_read_notifications_are_deleted(self):
        self._make(read=True, age_days=120)
        call_command("prune_notifications", days=90)
        self.assertEqual(Notification.objects.count(), 0)

    def test_an_unread_notification_is_kept_however_old(self):
        """It is still owed to somebody; deleting it drops the one thing the
        inbox exists to guarantee."""
        self._make(read=False, age_days=999)
        call_command("prune_notifications", days=90)
        self.assertEqual(Notification.objects.count(), 1)

    def test_a_recently_read_notification_is_kept(self):
        self._make(read=True, age_days=5)
        call_command("prune_notifications", days=90)
        self.assertEqual(Notification.objects.count(), 1)

    def test_a_dry_run_deletes_nothing(self):
        self._make(read=True, age_days=120)
        call_command("prune_notifications", days=90, dry_run=True)
        self.assertEqual(Notification.objects.count(), 1)

    def test_a_zero_day_window_is_refused(self):
        """It would delete a notification the moment it was read, which is
        indistinguishable from losing it."""
        self._make(read=True, age_days=1)
        call_command("prune_notifications", days=0)
        self.assertEqual(Notification.objects.count(), 1)


class VocabularyCoverageTests(EmissionBaseTest):
    def test_every_verb_has_an_emission_point(self):
        """Exercise each path and assert the whole vocabulary is reachable.

        This is the test that would have caught the five dead verbs.
        """
        # assignment, submission, review
        assign_task_to_annotator(self.task, annotator=self.other, actor=self.manager)
        assign_task_to_annotator(
            self.task, annotator=self.annotator, actor=self.manager
        )
        submission = submit_annotation(
            task=self.task, annotator=self.annotator, label_file=label_upload()
        )
        from annotation.services import reject_submission

        reject_submission(submission, reviewer=self.manager, comments="no")

        # hard cases
        case = create_hard_case(task=self.task, user=self.annotator, label_id=4)
        add_hard_case_message(case, user=self.manager, body="hi")

        # withdrawal
        withdraw_project_assignments(self.project, team_name="T")

        # deadlines + milestone
        AnnotationTask.objects.filter(pk=self.task.pk).update(
            assigned_to=self.annotator,
            deadline=timezone.localdate(),
            status=TaskStatus.ASSIGNED,
        )
        Milestone.objects.create(
            project=self.project, name="M",
            due_on=timezone.localdate(), target_value=9,
        )
        call_command("notify_deadlines", days=3)

        emitted = set(Notification.objects.values_list("verb", flat=True))
        expected = {verb.value for verb in NotificationVerb} - {
            # Written by `_after_approval` only when a reviewer's corrected
            # submission is approved over the annotator's, which needs the
            # quality feature on and two real label volumes — covered by
            # annotation.test_service_hooks instead.
            NotificationVerb.QUALITY_FLAGGED.value,
        }
        self.assertEqual(
            expected - emitted, set(),
            f"verbs defined but never emitted: {sorted(expected - emitted)}",
        )
