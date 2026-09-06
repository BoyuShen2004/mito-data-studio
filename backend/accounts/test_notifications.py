"""The per-person inbox.

Two properties matter most and are asserted repeatedly below:

* recording is independent of the API flag, so enabling the inbox later shows
  the history rather than starting empty;
* a failed inbox write never fails the action that triggered it.
"""

from __future__ import annotations

from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Notification, UserProfile
from accounts.notifications import (
    inbox,
    manager_recipients,
    mark_read,
    notify,
    notify_many,
    unread_count,
)
from core.choices import NotificationVerb, UserRole

ENABLED = override_settings(FEATURE_NOTIFICATIONS=True)


def make_user(username, role=UserRole.ANNOTATOR, **kwargs):
    user = User.objects.create_user(username=username, password="pw", **kwargs)
    UserProfile.objects.update_or_create(user=user, defaults={"role": role})
    # Refetch: the profile is auto-created with the default annotator role and
    # cached on the instance, so `update_or_create` changes the row while this
    # object still reports "annotator" — and every `is_manager` check reading
    # it would silently test the wrong role. Same trap documented in
    # volumes/test_chunk_service.py.
    return User.objects.get(pk=user.pk)


class NotifyTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")

    def test_a_notification_lands_in_the_recipients_inbox(self):
        notify(self.alice, NotificationVerb.TASK_ASSIGNED, title="Hello")
        self.assertEqual(unread_count(self.alice), 1)
        self.assertEqual(unread_count(self.bob), 0)

    def test_nobody_is_notified_about_their_own_action(self):
        """Callers should not have to filter the actor out at every call site."""
        notify(
            self.alice, NotificationVerb.SUBMISSION_RECEIVED,
            title="You submitted", actor=self.alice,
        )
        self.assertEqual(unread_count(self.alice), 0)

    def test_bulk_writes_apply_the_same_self_notify_rule(self):
        written = notify_many([
            {"recipient": self.alice, "verb": NotificationVerb.TASK_ASSIGNED,
             "title": "a", "actor": self.alice},
            {"recipient": self.bob, "verb": NotificationVerb.TASK_ASSIGNED,
             "title": "b", "actor": self.alice},
        ])
        self.assertEqual(written, 1)
        self.assertEqual(unread_count(self.bob), 1)

    def test_a_write_failure_is_swallowed_not_raised(self):
        """A lost notification must never turn a successful action into a 500."""
        with mock.patch.object(
            Notification.objects, "create", side_effect=RuntimeError("db down")
        ):
            result = notify(self.alice, NotificationVerb.TASK_ASSIGNED, title="x")
        self.assertIsNone(result)

    def test_over_long_text_is_truncated_rather_than_rejected(self):
        row = notify(
            self.alice, NotificationVerb.TASK_ASSIGNED,
            title="t" * 500, body="b" * 900, url="/u" * 400,
        )
        self.assertLessEqual(len(row.title), 200)
        self.assertLessEqual(len(row.body), 500)
        self.assertLessEqual(len(row.url), 300)


class MarkReadTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.mine = notify(self.alice, NotificationVerb.TASK_ASSIGNED, title="mine")
        self.theirs = notify(self.bob, NotificationVerb.TASK_ASSIGNED, title="theirs")

    def test_marking_read_is_scoped_to_the_caller(self):
        """Passing somebody else's id must mark nothing, not their inbox."""
        changed = mark_read(self.alice, [self.theirs.pk])
        self.assertEqual(changed, 0)
        self.theirs.refresh_from_db()
        self.assertIsNone(self.theirs.read_at)

    def test_marking_everything_read_clears_only_the_callers_inbox(self):
        mark_read(self.alice)
        self.assertEqual(unread_count(self.alice), 0)
        self.assertEqual(unread_count(self.bob), 1)

    def test_read_at_records_first_reading_not_the_latest(self):
        """A re-read must not push the timestamp forward."""
        mark_read(self.alice)
        self.mine.refresh_from_db()
        first = self.mine.read_at
        mark_read(self.alice)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.read_at, first)


class ManagerRecipientTests(TestCase):
    def test_superusers_and_managers_are_both_included_once(self):
        manager = make_user("mgr", UserRole.MANAGER)
        superuser = make_user("root", UserRole.MANAGER, is_superuser=True)
        make_user("ann", UserRole.ANNOTATOR)
        recipients = set(manager_recipients().values_list("username", flat=True))
        self.assertEqual(recipients, {"mgr", "root"})

    def test_inactive_accounts_are_not_notified(self):
        make_user("gone", UserRole.MANAGER, is_active=False)
        self.assertEqual(manager_recipients().count(), 0)


class ApiTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.client = APIClient()
        self.client.force_authenticate(self.alice)
        notify(self.alice, NotificationVerb.TASK_ASSIGNED, title="one", url="/tasks/1")

    @ENABLED
    def test_the_inbox_lists_and_counts(self):
        response = self.client.get("/api/notifications/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["unread"], 1)
        self.assertEqual(response.data["results"][0]["title"], "one")

    @ENABLED
    def test_the_badge_endpoint_is_just_a_count(self):
        response = self.client.get("/api/notifications/unread-count/")
        self.assertEqual(response.data, {"unread": 1})

    @ENABLED
    def test_marking_read_updates_the_count(self):
        response = self.client.post("/api/notifications/read/", {}, format="json")
        self.assertEqual(response.data["marked"], 1)
        self.assertEqual(response.data["unread"], 0)

    @ENABLED
    def test_a_non_list_ids_payload_is_a_400(self):
        response = self.client.post(
            "/api/notifications/read/", {"ids": "all"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    @ENABLED
    def test_the_page_size_is_capped(self):
        response = self.client.get("/api/notifications/?limit=99999")
        self.assertLessEqual(response.data["limit"], 200)

    def test_the_api_reports_disabled_while_recording_continues(self):
        """The flag gates reading, never writing.

        Turning the inbox on later must reveal what accumulated, which is only
        true if the service layer kept writing while the API was off.

        The "off" half is pinned explicitly rather than left to the default:
        this test used to rely on `FEATURE_NOTIFICATIONS` being off in the
        ambient environment, so it started failing the moment the flag was
        enabled in a developer's `.env`. A test must assert the same thing
        whatever the host is configured to do.
        """
        with override_settings(FEATURE_NOTIFICATIONS=False):
            response = self.client.get("/api/notifications/")
            self.assertEqual(response.status_code, 503)
            notify(
                self.alice, NotificationVerb.TASK_ASSIGNED, title="written anyway"
            )
        self.assertEqual(Notification.objects.filter(recipient=self.alice).count(), 2)
        with override_settings(FEATURE_NOTIFICATIONS=True):
            revealed = self.client.get("/api/notifications/")
        self.assertEqual(len(revealed.data["results"]), 2)
