"""Tests for the Manager Admin: access control, actions, and audit protection."""

import io
import os
import tempfile

import numpy as np
import tifffile
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.models import Count
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import AnnotatorProfile, Institution, Team, UserProfile
from accounts.teams import add_team_member, grant_project_team
from annotation.models import AnnotationTask, ReviewRecord
from annotation.services import (
    assign_task_to_annotator,
    create_whole_volume_task,
    submit_annotation,
)
from core.admin_common import lock_rows
from core.choices import TaskStatus, UserRole
from projects.services import create_project
from volumes.services import register_volume

User = get_user_model()

_TMP_ROOT = tempfile.mkdtemp(prefix="mito_admin_test_")

CHANGELISTS = [
    "admin:projects_project_changelist",
    "admin:volumes_volume_changelist",
    "admin:annotation_annotationtask_changelist",
    "admin:annotation_annotationsubmission_changelist",
    "admin:annotation_reviewrecord_changelist",
    "admin:accounts_institution_changelist",
    "admin:accounts_userprofile_changelist",
    "admin:accounts_annotatorprofile_changelist",
]


def make_user(username, role=None, is_staff=False, is_superuser=False):
    user = User.objects.create_user(
        username=username, password="pw", is_staff=is_staff, is_superuser=is_superuser
    )
    if role is not None:
        UserProfile.objects.update_or_create(user=user, defaults={"role": role})
    return user


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class AdminAccessTests(TestCase):
    def setUp(self):
        self.superuser = make_user("root", is_staff=True, is_superuser=True)
        self.manager = make_user("mgr", role=UserRole.MANAGER, is_staff=True)
        self.annotator = make_user("ann", role=UserRole.ANNOTATOR, is_staff=False)
        self.requester = make_user("req", role=UserRole.REQUESTER, is_staff=False)

    def test_manager_can_access_index_with_dashboard(self):
        self.client.force_login(self.manager)
        res = self.client.get(reverse("admin:index"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Operational dashboard")
        self.assertContains(res, "Mito Data Agent Manager")

    def test_manager_can_open_every_changelist(self):
        self.client.force_login(self.manager)
        for name in CHANGELISTS:
            res = self.client.get(reverse(name))
            self.assertEqual(res.status_code, 200, f"{name} -> {res.status_code}")

    def test_superuser_can_access(self):
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.assertEqual(
            self.client.get(
                reverse("admin:projects_project_changelist")
            ).status_code,
            200,
        )

    def test_annotator_denied(self):
        self.client.force_login(self.annotator)
        res = self.client.get(reverse("admin:projects_project_changelist"))
        self.assertEqual(res.status_code, 302)  # redirected to admin login

    def test_requester_denied(self):
        self.client.force_login(self.requester)
        res = self.client.get(reverse("admin:index"))
        self.assertEqual(res.status_code, 302)

    def test_staff_without_manager_role_denied(self):
        staff_annotator = make_user(
            "staffann", role=UserRole.ANNOTATOR, is_staff=True
        )
        self.client.force_login(staff_annotator)
        res = self.client.get(reverse("admin:index"))
        self.assertEqual(res.status_code, 302)


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class AdminActionTests(TestCase):
    def setUp(self):
        self.manager = make_user("mgr", role=UserRole.MANAGER, is_staff=True)
        self.client.force_login(self.manager)
        self.annotator = make_user("ann", role=UserRole.ANNOTATOR)
        AnnotatorProfile.objects.create(
            user=self.annotator, is_active_annotator=True, max_active_tasks=10
        )

    def _volume(self, project, name="v", shape_z=32):
        vol = register_volume(
            project=project, name=name, image_path=f"{name}.tiff",
            autodetect_shape=False,
        )
        vol.shape_x, vol.shape_y, vol.shape_z = 16, 16, shape_z
        vol.save()
        return vol

    # --- project approval ---------------------------------------------------
    def test_approve_projects_action(self):
        project = create_project(title="Pending", created_by=None)
        self.assertFalse(project.manager_reviewed)
        self.client.post(
            reverse("admin:projects_project_changelist"),
            {
                "action": "approve_projects",
                ACTION_CHECKBOX_NAME: [project.pk],
            },
        )
        project.refresh_from_db()
        self.assertTrue(project.manager_reviewed)
        self.assertEqual(project.reviewed_by_id, self.manager.id)

    # --- turning volumes into tasks -----------------------------------------
    def test_task_action_requires_approved_project(self):
        pending = create_project(title="Pending")  # not reviewed
        vol = self._volume(pending)
        self.client.post(
            reverse("admin:volumes_volume_changelist"),
            {"action": "create_whole_volume_tasks", ACTION_CHECKBOX_NAME: [vol.pk]},
        )
        self.assertEqual(vol.tasks.count(), 0)

    def test_create_whole_volume_task_action(self):
        project = create_project(title="Approved", reviewed=True)
        vol = self._volume(project, shape_z=32)
        self.client.post(
            reverse("admin:volumes_volume_changelist"),
            {"action": "create_whole_volume_tasks", ACTION_CHECKBOX_NAME: [vol.pk]},
        )
        self.assertEqual(vol.tasks.count(), 1)
        task = vol.tasks.first()
        self.assertEqual((task.z_start, task.z_end), (0, vol.shape_z))
        # Re-running does not add a second task to the same volume.
        self.client.post(
            reverse("admin:volumes_volume_changelist"),
            {"action": "create_whole_volume_tasks", ACTION_CHECKBOX_NAME: [vol.pk]},
        )
        self.assertEqual(vol.tasks.count(), 1)

    def test_admin_offers_no_frame_splitting_action(self):
        from volumes.admin import VolumeAdmin

        self.assertNotIn("split_into_frame_tasks", VolumeAdmin.actions)
        self.assertFalse(hasattr(VolumeAdmin, "split_into_frame_tasks"))

    # --- auto assignment ----------------------------------------------------
    def _assignment_team(self, project, *users):
        """Give ``project`` a working team containing ``users``.

        Assignment is team-first: `accounts.teams.is_eligible_project_assignee`
        requires the candidate to be a member of the project's one working
        team, and it deliberately does *not* consult `FEATURE_TEAMS` — team
        membership is the write-path invariant in every profile. A project with
        no working team therefore has no eligible assignees at all, which is
        why this fixture is required rather than incidental.
        """
        organization = Institution.objects.create(name=f"Org for {project.title}")
        team = Team.objects.create(organization=organization, name="Assignment")
        for user in users:
            add_team_member(team, user)
        grant_project_team(project, team)
        return team

    def test_auto_assign_action(self):
        project = create_project(title="Approved", reviewed=True)
        self._assignment_team(project, self.annotator)
        self._volume(project, name="a")
        self._volume(project, name="b")
        self.client.post(
            reverse("admin:projects_project_changelist"),
            {"action": "auto_assign_selected", ACTION_CHECKBOX_NAME: [project.pk]},
        )
        tasks = AnnotationTask.objects.filter(project=project)
        self.assertEqual(tasks.count(), 2)
        self.assertEqual(tasks.filter(assigned_to=self.annotator).count(), 2)

    # --- manual assignment form + capacity ----------------------------------
    def test_manual_assign_form_respects_capacity(self):
        capped = make_user("cap", role=UserRole.ANNOTATOR)
        AnnotatorProfile.objects.create(
            user=capped, is_active_annotator=True, max_active_tasks=1
        )
        project = create_project(title="Approved", reviewed=True)
        # Two volumes, so two assignable tasks — one per volume.
        tasks = [
            create_whole_volume_task(self._volume(project, name="a")),
            create_whole_volume_task(self._volume(project, name="b")),
        ]
        self.client.post(
            reverse("admin:annotation_annotationtask_changelist"),
            {
                "action": "assign_to_annotator",
                ACTION_CHECKBOX_NAME: [t.pk for t in tasks],
                "apply": "Assign tasks",
                "annotator": capped.pk,
            },
        )
        assigned = AnnotationTask.objects.filter(assigned_to=capped).count()
        self.assertEqual(assigned, 1)  # capacity 1 respected
        self.assertEqual(
            AnnotationTask.objects.filter(status=TaskStatus.UNASSIGNED).count(), 1
        )

    def test_unassign_action(self):
        project = create_project(title="Approved", reviewed=True)
        vol = self._volume(project)
        task = create_whole_volume_task(vol)
        assign_task_to_annotator(task, annotator=self.annotator)
        self.client.post(
            reverse("admin:annotation_annotationtask_changelist"),
            {"action": "unassign_tasks", ACTION_CHECKBOX_NAME: [task.pk]},
        )
        task.refresh_from_db()
        self.assertIsNone(task.assigned_to_id)
        self.assertEqual(task.status, TaskStatus.UNASSIGNED)


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class AdminReviewTests(TestCase):
    def setUp(self):
        self.manager = make_user("mgr", role=UserRole.MANAGER, is_staff=True)
        self.client.force_login(self.manager)
        self.annotator = make_user("ann", role=UserRole.ANNOTATOR)
        AnnotatorProfile.objects.create(user=self.annotator, is_active_annotator=True)
        project = create_project(title="Approved", reviewed=True)
        # Real raw data on disk: approve re-seeds the working draft from the
        # new official label, and that reset reads the registered image.
        tifffile.imwrite(
            os.path.join(_TMP_ROOT, "v.tiff"), np.zeros((16, 8, 8), dtype=np.uint8)
        )
        vol = register_volume(
            project=project, name="v", image_path="v.tiff", autodetect_shape=False
        )
        vol.shape_x, vol.shape_y, vol.shape_z = 8, 8, 16
        vol.save()
        self.task = create_whole_volume_task(vol)
        assign_task_to_annotator(self.task, annotator=self.annotator)
        # A readable mask of the registered shape: approving an upload now
        # installs it as the volume's official label, so a placeholder byte
        # string would fail validation rather than exercise the admin action.
        payload = io.BytesIO()
        tifffile.imwrite(payload, np.zeros((16, 8, 8), dtype=np.uint16))
        upload = SimpleUploadedFile("label.tif", payload.getvalue())
        self.submission = submit_annotation(
            task=self.task, annotator=self.annotator, label_file=upload
        )

    def test_approve_submission_action(self):
        self.client.post(
            reverse("admin:annotation_annotationsubmission_changelist"),
            {"action": "approve_selected", ACTION_CHECKBOX_NAME: [self.submission.pk]},
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.APPROVED)
        self.assertEqual(self.submission.reviews.count(), 1)

    def test_reject_requires_comments(self):
        url = reverse("admin:annotation_annotationsubmission_changelist")
        # Empty comment -> intermediate form re-rendered, no review created.
        res = self.client.post(
            url,
            {
                "action": "reject_selected",
                ACTION_CHECKBOX_NAME: [self.submission.pk],
                "apply": "Confirm",
                "comments": "",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(ReviewRecord.objects.count(), 0)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.SUBMITTED)

        # With a comment -> review recorded, task rejected.
        self.client.post(
            url,
            {
                "action": "reject_selected",
                ACTION_CHECKBOX_NAME: [self.submission.pk],
                "apply": "Confirm",
                "comments": "Needs cleaner boundaries.",
            },
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.REJECTED)
        self.assertEqual(ReviewRecord.objects.count(), 1)

    def test_request_revision_action(self):
        self.client.post(
            reverse("admin:annotation_annotationsubmission_changelist"),
            {
                "action": "request_revision_selected",
                ACTION_CHECKBOX_NAME: [self.submission.pk],
                "apply": "Confirm",
                "comments": "Please fix z-slice 4.",
            },
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.REVISION_REQUESTED)


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class AdminAuditProtectionTests(TestCase):
    def setUp(self):
        self.manager = make_user("mgr", role=UserRole.MANAGER, is_staff=True)
        self.superuser = make_user("root", is_staff=True, is_superuser=True)
        from annotation.admin import ReviewRecordAdmin
        from django.contrib import admin as dj_admin

        self.review_admin = ReviewRecordAdmin(ReviewRecord, dj_admin.site)

    def _request(self, user):
        from django.test import RequestFactory

        request = RequestFactory().get("/admin/")
        request.user = user
        return request

    def test_review_records_not_addable_or_editable_by_manager(self):
        req = self._request(self.manager)
        self.assertFalse(self.review_admin.has_add_permission(req))
        self.assertFalse(self.review_admin.has_change_permission(req))
        self.assertFalse(self.review_admin.has_delete_permission(req))

    def test_superuser_may_delete_review_records(self):
        req = self._request(self.superuser)
        self.assertTrue(self.review_admin.has_delete_permission(req))

    def test_manager_cannot_add_review_record_via_client(self):
        self.client.force_login(self.manager)
        res = self.client.get(reverse("admin:annotation_reviewrecord_add"))
        self.assertEqual(res.status_code, 403)


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class LockRowsTests(TestCase):
    """Regression: ``FOR UPDATE is not allowed with GROUP BY clause``.

    Admin ``get_queryset`` overrides annotate counts for display columns, which
    adds a GROUP BY. Calling ``.select_for_update()`` on that queryset is a hard
    error on PostgreSQL, and a silent no-op on SQLite — so for years the affected
    admin actions took *no lock at all* while appearing to work.

    The admin action tests cover this only incidentally, and only when the suite
    runs on PostgreSQL. These assert the property directly, on any engine.
    """

    def setUp(self):
        self.project = create_project(title="Locked")
        self.volume = register_volume(
            project=self.project, name="v", image_path="v.tiff",
            autodetect_shape=False,
        )
        self.tasks = [
            AnnotationTask.objects.create(
                project=self.project, volume=self.volume,
                z_start=i, z_end=i + 1, y_end=16, x_end=16,
                status=TaskStatus.UNASSIGNED,
            )
            for i in range(3)
        ]

    def _annotated(self):
        """The shape an admin changelist actually hands to an action."""
        return AnnotationTask.objects.annotate(
            _submissions=Count("submissions", distinct=True)
        )

    def test_annotated_queryset_really_does_group(self):
        # Guard the premise: if this stops being true the test below is vacuous.
        self.assertIn("GROUP BY", str(self._annotated().query).upper())

    def test_lock_rows_drops_the_grouping(self):
        self.assertNotIn("GROUP BY", str(lock_rows(self._annotated()).query).upper())

    def test_lock_rows_selects_the_same_rows(self):
        with transaction.atomic():
            locked = list(lock_rows(self._annotated()))
        self.assertCountEqual(
            [t.pk for t in locked], [t.pk for t in self.tasks]
        )

    def test_lock_rows_executes_where_select_for_update_would_fail(self):
        """The exact call that raised FeatureNotSupported before the fix."""
        with transaction.atomic():
            rows = list(lock_rows(self._annotated().filter(pk=self.tasks[0].pk)))
        self.assertEqual([r.pk for r in rows], [self.tasks[0].pk])

    def test_lock_rows_on_empty_selection_is_harmless(self):
        with transaction.atomic():
            self.assertEqual(
                list(lock_rows(self._annotated().none())), []
            )
