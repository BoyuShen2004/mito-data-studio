from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import AnnotatorProfile, UserProfile
from annotation.models import AnnotationSubmission, AnnotationTask, ReviewLabelComment
from core.choices import TaskStatus, TaskType, UserRole
from projects.services import create_project
from volumes.models import Volume


User = get_user_model()


class ReviewLabelCommentApiTests(TestCase):
    def setUp(self):
        self.manager = self._user("comment_mgr", UserRole.MANAGER)
        self.other_manager = self._user("comment_mgr_2", UserRole.MANAGER)
        self.annotator = self._user("comment_ann", UserRole.ANNOTATOR, annotator=True)
        self.outsider = self._user("comment_out", UserRole.ANNOTATOR, annotator=True)
        project = create_project(
            title="Comment review", created_by=self.manager, reviewed=True
        )
        volume = Volume.objects.create(
            project=project,
            name="comment-volume",
            shape_z=4,
            shape_y=8,
            shape_x=8,
        )
        self.task = AnnotationTask.objects.create(
            project=project,
            volume=volume,
            assigned_to=self.annotator,
            z_start=0,
            z_end=4,
            y_end=8,
            x_end=8,
            task_type=TaskType.MANUAL_ANNOTATION,
            status=TaskStatus.SUBMITTED,
        )
        self.submission = AnnotationSubmission.objects.create(
            task=self.task, annotator=self.annotator, source="inapp"
        )
        self.url = "/api/review-label-comments/"

    def _user(self, name, role, annotator=False):
        user = User.objects.create_user(name, password="x")
        UserProfile.objects.filter(user=user).update(role=role)
        if annotator:
            AnnotatorProfile.objects.create(user=user, is_active_annotator=True)
        return User.objects.get(pk=user.pk)

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _create(self, label_id=7, body="split is too coarse"):
        return self._client(self.manager).post(
            self.url,
            {"submission": self.submission.id, "task": self.task.id, "label_id": label_id, "body": body},
            format="json",
        )

    def test_manager_can_create_and_edit_one_comment_per_label(self):
        created = self._client(self.manager).post(
            self.url,
            {
                "submission": self.submission.id,
                "task": self.task.id,
                "label_id": 7,
                "body": "split is too coarse",
                "view_z": 2,
                "view_y": 3,
                "view_x": 4,
                "view_axis": "z",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["view_z"], 2)
        self.assertEqual(created.json()["view_y"], 3)
        self.assertEqual(created.json()["view_x"], 4)
        self.assertEqual(created.json()["view_axis"], "z")
        edited = self._create(body="follow the membrane")
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertEqual(ReviewLabelComment.objects.count(), 1)
        self.assertEqual(edited.json()["body"], "follow the membrane")
        self.assertEqual(edited.json()["task"], self.task.id)

    def test_only_authoring_manager_can_edit_and_annotators_cannot_create(self):
        self._create()
        other = self._client(self.other_manager).post(
            self.url,
            {"submission": self.submission.id, "task": self.task.id, "label_id": 7, "body": "overwrite"},
            format="json",
        )
        self.assertEqual(other.status_code, 403)
        denied = self._client(self.annotator).post(
            self.url,
            {"submission": self.submission.id, "task": self.task.id, "label_id": 8, "body": "no"},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

    def test_annotator_sees_feedback_only_after_review_and_outsider_never_does(self):
        self._create()
        before = self._client(self.annotator).get(self.url).json()
        self.assertEqual(before, [])

        decision = self._client(self.manager).post(
            f"/api/submissions/{self.submission.id}/review/",
            {"decision": "revision_requested", "comments": "see labels"},
            format="json",
        )
        self.assertEqual(decision.status_code, 200, decision.content)
        after = self._client(self.annotator).get(self.url).json()
        self.assertEqual([row["label_id"] for row in after], [7])
        self.assertEqual(self._client(self.outsider).get(self.url).json(), [])

    def test_decision_preserves_comments_and_closes_editing(self):
        self._create(label_id=12)
        decision = self._client(self.manager).post(
            f"/api/submissions/{self.submission.id}/review/",
            {"decision": "rejected"},
            format="json",
        )
        self.assertEqual(decision.status_code, 200, decision.content)
        self.assertTrue(
            ReviewLabelComment.objects.filter(
                submission=self.submission, label_id=12
            ).exists()
        )
        closed = self._create(label_id=12, body="late edit")
        self.assertEqual(closed.status_code, 409)

    def test_submission_payload_exposes_comment_count(self):
        self._create(label_id=3)
        self._create(label_id=4)
        detail = self._client(self.manager).get(
            f"/api/submissions/{self.submission.id}/"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["label_comment_count"], 2)

    @mock.patch("annotation.services._install_submission_as_official")
    def test_approve_also_preserves_comments(self, install):
        self._create(label_id=21, body="approved-round note")
        decision = self._client(self.manager).post(
            f"/api/submissions/{self.submission.id}/review/",
            {"decision": "approved"},
            format="json",
        )
        self.assertEqual(decision.status_code, 200, decision.content)
        install.assert_called_once()
        self.assertTrue(
            ReviewLabelComment.objects.filter(
                submission=self.submission,
                label_id=21,
                body="approved-round note",
            ).exists()
        )

    def test_manager_can_delete_comment_and_annotator_cannot(self):
        created = self._create(label_id=9, body="remove me")
        comment_id = created.json()["id"]
        forbidden = self._client(self.annotator).delete(
            f"/api/review-label-comments/{comment_id}/"
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertTrue(ReviewLabelComment.objects.filter(pk=comment_id).exists())
        deleted = self._client(self.manager).delete(
            f"/api/review-label-comments/{comment_id}/"
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(ReviewLabelComment.objects.filter(pk=comment_id).exists())
