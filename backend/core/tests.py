import tempfile
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import AnnotatorProfile, Institution, Team, TeamMembership
from annotation.models import AnnotationTask
from core.choices import TeamRole, UserRole
from core.dev_data import (
    DEV_ORGANIZATION_NAME,
    STANDARD_ACCOUNTS,
    clear_dev_data,
    data_summary,
    seed_standard_data,
)
from projects.models import Project

User = get_user_model()

# clear_dev_data() wipes everything under MITO_DATA_ROOT (see dev_data.py) —
# without overriding it to a throwaway tempdir here, these tests would wipe
# the *real* dev data/ directory every time the suite runs. Same pattern as
# annotation/tests.py and friends.
_TMP_ROOT = tempfile.mkdtemp(prefix="mito_devdata_test_")


@override_settings(DEBUG=True, MITO_DATA_ROOT=_TMP_ROOT)
class DevDataCommandTests(TestCase):
    def test_seed_creates_accounts_and_default_assignment_team_only(self):
        seed_standard_data(log=lambda *a, **k: None)

        # One manager (superuser) + four annotators, and no pre-registered data.
        self.assertTrue(User.objects.get(username="manager").is_superuser)
        annotators = [
            n for n, r in STANDARD_ACCOUNTS.items() if r == UserRole.ANNOTATOR
        ]
        self.assertEqual(len(annotators), 4)
        for name in annotators:
            user = User.objects.get(username=name)
            self.assertFalse(user.is_superuser)
            self.assertTrue(AnnotatorProfile.objects.filter(user=user).exists())

        organization = Institution.objects.get(name=DEV_ORGANIZATION_NAME)
        team = Team.objects.get(organization=organization, is_default=True)
        memberships = {
            membership.user.username: membership.role
            for membership in TeamMembership.objects.filter(team=team).select_related(
                "user"
            )
        }
        self.assertEqual(
            {name for name in memberships if name != "manager"}, set(annotators)
        )
        self.assertEqual(memberships["manager"], TeamRole.MANAGER)

        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(AnnotationTask.objects.count(), 0)

    def test_rerunning_seed_grants_default_team_to_existing_projects(self):
        seed_standard_data(log=lambda *a, **k: None)
        project = Project.objects.create(title="manual", dataset="manual")
        self.assertEqual(project.teams.count(), 0)

        seed_standard_data(log=lambda *a, **k: None)

        project.refresh_from_db()
        team = Team.objects.get(
            organization__name=DEV_ORGANIZATION_NAME, is_default=True
        )
        self.assertTrue(project.teams.filter(pk=team.pk).exists())

    def test_rerunning_seed_reactivates_dev_annotators(self):
        seed_standard_data(log=lambda *a, **k: None)
        alice = User.objects.get(username="alice")
        alice.is_active = False
        alice.save(update_fields=["is_active"])
        alice.annotator_profile.is_active_annotator = False
        alice.annotator_profile.max_active_tasks = 0
        alice.annotator_profile.save(
            update_fields=["is_active_annotator", "max_active_tasks"]
        )

        seed_standard_data(log=lambda *a, **k: None)

        alice.refresh_from_db()
        alice.annotator_profile.refresh_from_db()
        self.assertTrue(alice.is_active)
        self.assertTrue(alice.annotator_profile.is_active_annotator)
        self.assertGreater(alice.annotator_profile.max_active_tasks, 0)

    @override_settings(MOCK_DEV_LOGIN_PASSWORD="configured-demo-password")
    def test_safe_mock_seed_uses_configured_password_without_admin_privilege(self):
        seed_standard_data(log=lambda *a, **k: None, safe_mock_login=True)
        self.assertEqual(User.objects.count(), 7)
        for user in User.objects.all():
            self.assertTrue(user.check_password("configured-demo-password"))
        manager = User.objects.get(username="manager")
        self.assertFalse(manager.is_staff)
        self.assertFalse(manager.is_superuser)
        self.assertEqual(manager.profile.role, UserRole.MANAGER)

    def test_clear_preserves_superusers(self):
        seed_standard_data(log=lambda *a, **k: None)
        # Simulate data a developer registered manually.
        Project.objects.create(title="manual", dataset="manual")

        clear_dev_data(log=lambda *a, **k: None)

        self.assertEqual(Project.objects.count(), 0)
        # Superuser manager survives; non-superuser annotators are removed.
        self.assertTrue(User.objects.filter(username="manager").exists())
        self.assertFalse(User.objects.filter(username="alice").exists())

    def test_clear_keep_users(self):
        seed_standard_data(log=lambda *a, **k: None)
        clear_dev_data(keep_users=True, log=lambda *a, **k: None)
        self.assertTrue(User.objects.filter(username="alice").exists())

    def test_clear_wipes_working_copy_files_not_just_filefields(self):
        """Regression test for a real bug: the in-app editor's working label
        copies (annotation/label_paths.py) are written directly by path, not
        through a Django FileField, so a per-FileField .delete() loop alone
        (the old implementation) never touched them — "Clear all data &
        reset" left them orphaned on disk. See
        progress/history/17-fix-dev-reset-orphaned-files.md.
        """
        import os

        # Stand in for a working-copy file (and an uploaded-file-style path)
        # written directly under MITO_DATA_ROOT, exactly as the app would —
        # not going through any model/FileField.
        working_copy = os.path.join(_TMP_ROOT, "some-project", "some-dataset", "volume_1_labels.tif")
        os.makedirs(os.path.dirname(working_copy), exist_ok=True)
        with open(working_copy, "wb") as f:
            f.write(b"fake tif bytes")

        clear_dev_data(log=lambda *a, **k: None)

        self.assertFalse(os.path.exists(working_copy))
        self.assertFalse(os.path.exists(os.path.join(_TMP_ROOT, "some-project")))

    def test_commands_run(self):
        call_command("seed_dev", "--fresh", stdout=StringIO())
        out = StringIO()
        call_command("dev_status", stdout=out)
        self.assertIn("projects", out.getvalue())
        self.assertEqual(data_summary()["projects"], 0)
        self.assertEqual(data_summary()["annotators"], 4)

        call_command("clear_dev_data", "--no-input", stdout=StringIO())
        self.assertEqual(data_summary()["annotators"], 0)
