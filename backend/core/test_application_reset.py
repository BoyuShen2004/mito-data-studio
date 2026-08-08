import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import Institution, Team, TeamMembership
from core.application_reset import (
    CONFIRM_PHRASE, ResetRefused, execute_reset, issue_confirmation,
    storage_manifest,
)
from core.models import ApplicationResetRecord
from processing.models import ProcessingJob
from projects.models import Dataset, Project, PublicShare
from volumes.models import Volume


class ApplicationResetTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir()
        self.backup = Path(self.tmp.name) / "backup"
        self.backup.mkdir()
        self.dump = self.backup / "db.dump"
        self.archive = self.backup / "data.tar"
        self.dump.write_bytes(b"verified database")
        self.archive.write_bytes(b"verified data")
        self.marker = self.backup / "verified.json"
        self.marker.write_text(json.dumps({
            "database_dump": str(self.dump), "data_archive": str(self.archive),
            "database_sha256": "a" * 64, "data_sha256": "b" * 64,
            "verified_at": "now",
        }))
        User = get_user_model()
        self.admin = User.objects.create_superuser("admin", password="strong-test-password")
        self.other = User.objects.create_user("test-user", password="strong-test-password")
        self.mock = User.objects.create_user("alice", password="strong-test-password")

    def tearDown(self):
        self.tmp.cleanup()

    def _settings(self):
        return override_settings(
            MITO_DATA_ROOT=self.root, MITO_MAINTENANCE_MODE=True,
            MITO_RESET_BACKUP_MARKER=str(self.marker),
            MITO_RESET_BACKUP_MAX_AGE_SECONDS=3600,
            MITO_RESET_ADMIN_USERNAME="admin",
            MOCK_DEV_LOGIN_ACCOUNTS=("alice",),
        )

    def _environment(self):
        from core.deployment import identity
        info = identity()
        return patch.dict(os.environ, {
            "MITO_EXPECTED_CHECKOUT": str(info["checkout"]),
            "MITO_EXPECTED_DATA_ROOT": str(self.root.resolve()),
            "MITO_EXPECTED_DB_NAME": str(info["database"]["name"]),
        })

    def test_manifest_distinguishes_external_and_app_owned(self):
        external = Path(self.tmp.name) / "external.tif"
        external.write_bytes(b"external")
        project = Project.objects.create(title="p", created_by=self.admin)
        dataset = Dataset.objects.create(project=project, name="d")
        Volume.objects.create(project=project, dataset=dataset, name="v", image_path=str(external))
        (self.root / "generated.tif").write_bytes(b"owned")
        with self._settings():
            manifest = storage_manifest()
        self.assertTrue(any(r["classification"] == "external source image" for r in manifest))
        self.assertTrue(any(r["classification"] == "app-owned generated data" for r in manifest))

    def test_reset_is_guarded_idempotent_and_preserves_external_bytes(self):
        external = Path(self.tmp.name) / "external.tif"
        external.write_bytes(b"do-not-delete")
        project = Project.objects.create(title="p", created_by=self.other)
        organization = Institution.objects.create(name="Reset team org")
        team = Team.objects.create(organization=organization, name="Reset me")
        TeamMembership.objects.create(team=team, user=self.mock)
        project.teams.add(team)
        dataset = Dataset.objects.create(project=project, name="d")
        volume = Volume.objects.create(project=project, dataset=dataset, name="v", image_path=str(external))
        PublicShare.objects.create(
            scope=PublicShare.Scope.VOLUME,
            project=project,
            dataset=dataset,
            volume=volume,
            created_by=self.mock,
        )
        job = ProcessingJob.objects.create(
            job_type="ingest",
            backend="local",
            status="queued",
            project=project,
            volume=volume,
            created_by=self.mock,
        )
        LogEntry.objects.create(
            user=self.admin,
            content_type=None,
            object_id=str(project.pk),
            object_repr="reset fixture",
            action_flag=ADDITION,
            change_message="",
        )
        (self.root / "working-mask.tif").write_bytes(b"owned")
        link = self.root / "registered-source"
        link.symlink_to(external)
        with self._settings(), self._environment():
            token = issue_confirmation(self.admin, CONFIRM_PHRASE)
            result = execute_reset(self.admin, token, CONFIRM_PHRASE)
            self.assertEqual(result["after"]["projects"], 0)
            self.assertEqual(result["before"]["teams"], 1)
            self.assertEqual(result["before"]["memberships"], 1)
            self.assertEqual(result["before"]["project_team_grants"], 1)
            self.assertEqual(result["after"]["teams"], 0)
            self.assertEqual(result["after"]["memberships"], 0)
            self.assertEqual(result["after"]["project_team_grants"], 0)
            self.assertEqual(result["after"]["datasets"], 0)
            self.assertEqual(result["after"]["volumes"], 0)
            self.assertEqual(result["after"]["public_shares"], 0)
            self.assertEqual(result["after"]["processing_jobs"], 0)
            self.assertEqual(result["after"]["admin_log_entries"], 0)
            self.assertEqual(result["cancelled_processing_jobs"], [job.pk])
            link_row = next(
                row for row in result["storage_manifest"]
                if row["field"] == "data_root_entry"
                and row["stored_path"] == "registered-source"
            )
            self.assertEqual(link_row["action"], "delete link only")
            with self.assertRaises(ResetRefused):
                execute_reset(self.admin, token, CONFIRM_PHRASE)
        self.assertEqual(external.read_bytes(), b"do-not-delete")
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(
            set(get_user_model().objects.values_list("username", flat=True)),
            {"admin", "alice"},
        )
        self.assertEqual(ApplicationResetRecord.objects.count(), 1)

    def test_refuses_without_maintenance(self):
        with override_settings(
            MITO_DATA_ROOT=self.root, MITO_MAINTENANCE_MODE=False,
            MITO_RESET_BACKUP_MARKER=str(self.marker), MITO_RESET_ADMIN_USERNAME="admin",
        ), self._environment(), self.assertRaises(ResetRefused):
            issue_confirmation(self.admin, CONFIRM_PHRASE)
