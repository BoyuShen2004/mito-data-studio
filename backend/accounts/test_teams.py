"""Teams, access audit, and the permission matrix.

Two properties matter most here and are asserted explicitly:

1. **With ``FEATURE_TEAMS`` off, nothing changes.** The tables exist and the
   services work, but no access decision differs from before Phase 1. That is
   what makes the migration safe to deploy ahead of the behaviour.
2. **Team access only ever widens.** Granting a team can add reach; it can
   never take away access someone already had.
"""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from accounts.audit import audit_trail, record_audit
from accounts.models import AuditEvent, Institution, Team, TeamMembership, UserProfile
from accounts.teams import (
    add_team_member,
    default_team_for,
    grant_project_team,
    has_project_team_access,
    is_team_manager,
    is_team_member,
    managed_teams,
    ensure_project_assignee_eligible,
    remove_team_member,
    revoke_project_team,
    set_project_working_team,
    user_teams,
)
from annotation.services import is_project_member
from core.choices import AuditVerb, MembershipSource, TeamRole, UserRole
from projects.models import Project, ProjectMembership


def make_user(username, role=UserRole.ANNOTATOR, institution=None):
    # A post_save signal (accounts.signals.ensure_user_profile) already made a
    # default profile, so set the role on that row rather than creating a second.
    user = User.objects.create_user(username, password="pw-for-tests-1")
    UserProfile.objects.update_or_create(
        user=user, defaults={"role": role, "institution": institution}
    )
    user.refresh_from_db()
    return user


class TeamMembershipTests(TestCase):
    def setUp(self):
        self.org = Institution.objects.create(name="Org A")
        self.team = Team.objects.create(organization=self.org, name="Team 1")
        self.annotator = make_user("ann")

    def test_membership_round_trip(self):
        self.assertFalse(is_team_member(self.annotator, self.team))
        add_team_member(self.team, self.annotator)
        self.assertTrue(is_team_member(self.annotator, self.team))
        self.assertIn(self.team, list(user_teams(self.annotator)))

        self.assertTrue(remove_team_member(self.team, self.annotator))
        self.assertFalse(is_team_member(self.annotator, self.team))

    def test_adding_twice_is_idempotent(self):
        add_team_member(self.team, self.annotator)
        add_team_member(self.team, self.annotator)
        self.assertEqual(TeamMembership.objects.filter(team=self.team).count(), 1)

    def test_re_adding_with_a_new_role_promotes(self):
        add_team_member(self.team, self.annotator, role=TeamRole.MEMBER)
        add_team_member(self.team, self.annotator, role=TeamRole.MANAGER)
        self.assertEqual(TeamMembership.objects.filter(team=self.team).count(), 1)
        self.assertTrue(is_team_manager(self.annotator, self.team))

    def test_removing_a_non_member_reports_nothing_removed(self):
        self.assertFalse(remove_team_member(self.team, self.annotator))

    def test_default_team_is_created_once(self):
        first = default_team_for(self.org)
        second = default_team_for(self.org)
        self.assertEqual(first.pk, second.pk)
        self.assertTrue(first.is_default)

    def test_team_names_are_unique_per_organisation_but_not_globally(self):
        other_org = Institution.objects.create(name="Org B")
        # Same name under a different org is fine.
        Team.objects.create(organization=other_org, name="Team 1")

        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            Team.objects.create(organization=self.org, name="Team 1")


class TeamRoleTests(TestCase):
    """Org role and team role are orthogonal."""

    def setUp(self):
        self.org = Institution.objects.create(name="Org")
        self.team = Team.objects.create(organization=self.org, name="T")
        self.manager = make_user("mgr", UserRole.MANAGER)
        self.team_manager = make_user("tmgr", UserRole.ANNOTATOR)
        self.annotator = make_user("ann", UserRole.ANNOTATOR)
        add_team_member(self.team, self.team_manager, role=TeamRole.MANAGER)
        add_team_member(self.team, self.annotator, role=TeamRole.MEMBER)

    def test_org_manager_manages_every_team_without_membership(self):
        self.assertTrue(is_team_manager(self.manager, self.team))
        self.assertFalse(is_team_member(self.manager, self.team))
        self.assertIn(self.team, list(managed_teams(self.manager)))

    def test_an_annotator_can_manage_one_team(self):
        self.assertTrue(is_team_manager(self.team_manager, self.team))
        self.assertEqual(self.team_manager.profile.role, UserRole.ANNOTATOR)

    def test_a_plain_member_does_not_manage(self):
        self.assertTrue(is_team_member(self.annotator, self.team))
        self.assertFalse(is_team_manager(self.annotator, self.team))
        self.assertEqual(list(managed_teams(self.annotator)), [])

    def test_anonymous_users_have_no_standing(self):
        from django.contrib.auth.models import AnonymousUser

        anon = AnonymousUser()
        self.assertFalse(is_team_member(anon, self.team))
        self.assertFalse(is_team_manager(anon, self.team))
        self.assertEqual(list(user_teams(anon)), [])


class ProjectTeamAccessTests(TestCase):
    def setUp(self):
        self.org = Institution.objects.create(name="Org")
        self.team = Team.objects.create(organization=self.org, name="T")
        self.other_team = Team.objects.create(organization=self.org, name="Other")

        self.owner = make_user("owner", UserRole.REQUESTER)
        self.member = make_user("member", UserRole.ANNOTATOR)
        self.outsider = make_user("outsider", UserRole.ANNOTATOR)
        add_team_member(self.team, self.member)
        add_team_member(self.other_team, self.outsider)

        self.project = Project.objects.create(title="P", created_by=self.owner)

    def test_a_project_with_no_teams_grants_nobody_team_access(self):
        """Absence of a grant is not a grant."""
        self.assertFalse(has_project_team_access(self.member, self.project))

    def test_granting_a_team_admits_its_members_only(self):
        grant_project_team(self.project, self.team)
        self.assertTrue(has_project_team_access(self.member, self.project))
        self.assertFalse(has_project_team_access(self.outsider, self.project))

    def test_revoking_removes_access(self):
        grant_project_team(self.project, self.team)
        revoke_project_team(self.project, self.team)
        self.assertFalse(has_project_team_access(self.member, self.project))

    def test_grant_is_idempotent(self):
        grant_project_team(self.project, self.team)
        grant_project_team(self.project, self.team)
        self.assertEqual(self.project.teams.count(), 1)


class FeatureFlagTests(TestCase):
    """The flag decides whether team grants affect real access decisions."""

    def setUp(self):
        self.org = Institution.objects.create(name="Org")
        self.team = Team.objects.create(organization=self.org, name="T")
        self.owner = make_user("owner", UserRole.REQUESTER)
        self.teammate = make_user("teammate", UserRole.ANNOTATOR)
        add_team_member(self.team, self.teammate)
        self.project = Project.objects.create(title="P", created_by=self.owner)
        grant_project_team(self.project, self.team)

    @override_settings(FEATURE_TEAMS=False)
    def test_flag_off_leaves_legacy_behaviour_untouched(self):
        # Granted a team, but the flag is off: no change from pre-Phase-1.
        self.assertFalse(is_project_member(self.teammate, self.project))

    @override_settings(FEATURE_TEAMS=True)
    def test_flag_on_admits_the_team(self):
        self.assertTrue(is_project_member(self.teammate, self.project))

    @override_settings(FEATURE_TEAMS=True)
    def test_team_access_only_widens_never_narrows(self):
        """Everyone who had access before still has it once teams are on."""
        # The owner reaches their own project without any team.
        self.assertTrue(is_project_member(self.owner, self.project))

        stranger = make_user("stranger", UserRole.ANNOTATOR)
        self.assertFalse(is_project_member(stranger, self.project))

    @override_settings(FEATURE_TEAMS=True)
    def test_a_project_with_no_grants_still_uses_the_legacy_rule(self):
        ungranted = Project.objects.create(title="Q", created_by=self.owner)
        self.assertTrue(is_project_member(self.owner, ungranted))
        self.assertFalse(is_project_member(self.teammate, ungranted))


class PermissionMatrixTests(TestCase):
    """The Phase 1 acceptance criterion: one table, four roles.

    Columns are the four roles the master prompt names; rows are the access
    questions Phase 1 is responsible for.
    """

    @classmethod
    def setUpTestData(cls):
        cls.org = Institution.objects.create(name="Org")
        cls.team = Team.objects.create(organization=cls.org, name="T")
        cls.manager = make_user("m", UserRole.MANAGER, cls.org)
        cls.team_manager = make_user("tm", UserRole.ANNOTATOR, cls.org)
        cls.annotator = make_user("a", UserRole.ANNOTATOR, cls.org)
        cls.requester = make_user("r", UserRole.REQUESTER, cls.org)

        add_team_member(cls.team, cls.team_manager, role=TeamRole.MANAGER)
        add_team_member(cls.team, cls.annotator, role=TeamRole.MEMBER)

        cls.project = Project.objects.create(title="P", created_by=cls.requester)
        grant_project_team(cls.project, cls.team)

    @override_settings(FEATURE_TEAMS=True)
    def test_project_membership_matrix(self):
        expected = {
            "m": True,   # org manager — everything
            "tm": True,  # team manager, via the granted team
            "a": True,   # team member, via the granted team
            "r": True,   # requester who created it
        }
        for username, allowed in expected.items():
            user = User.objects.get(username=username)
            with self.subTest(user=username):
                self.assertEqual(is_project_member(user, self.project), allowed)

    @override_settings(FEATURE_TEAMS=True)
    def test_outsiders_are_excluded_regardless_of_role(self):
        for role in (UserRole.ANNOTATOR, UserRole.REQUESTER):
            outsider = make_user(f"out-{role}", role, self.org)
            with self.subTest(role=role):
                self.assertFalse(is_project_member(outsider, self.project))

    @override_settings(FEATURE_TEAMS=True)
    def test_team_management_matrix(self):
        expected = {"m": True, "tm": True, "a": False, "r": False}
        for username, allowed in expected.items():
            user = User.objects.get(username=username)
            with self.subTest(user=username):
                self.assertEqual(is_team_manager(user, self.team), allowed)

    @override_settings(FEATURE_TEAMS=True)
    def test_an_org_manager_needs_no_membership(self):
        self.assertFalse(is_team_member(self.manager, self.team))
        self.assertTrue(is_team_manager(self.manager, self.team))


class AuditTests(TestCase):
    def setUp(self):
        self.org = Institution.objects.create(name="Org")
        self.team = Team.objects.create(organization=self.org, name="T")
        self.actor = make_user("actor", UserRole.MANAGER)
        self.subject = make_user("subject")

    def test_membership_changes_are_audited(self):
        add_team_member(self.team, self.subject, actor=self.actor)
        event = AuditEvent.objects.filter(verb=AuditVerb.TEAM_MEMBER_ADDED).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.actor)
        self.assertEqual(event.target_type, "Team")
        self.assertEqual(event.metadata["username"], "subject")

    def test_role_change_records_the_previous_role(self):
        add_team_member(self.team, self.subject, role=TeamRole.MEMBER, actor=self.actor)
        add_team_member(self.team, self.subject, role=TeamRole.MANAGER, actor=self.actor)
        event = AuditEvent.objects.filter(verb=AuditVerb.TEAM_ROLE_CHANGED).first()
        self.assertEqual(event.metadata["previous_role"], TeamRole.MEMBER)
        self.assertEqual(event.metadata["role"], TeamRole.MANAGER)

    def test_an_unauthenticated_actor_is_recorded_as_system(self):
        from django.contrib.auth.models import AnonymousUser

        event = record_audit(AnonymousUser(), AuditVerb.PERMISSION_DENIED, self.team)
        self.assertIsNone(event.actor)

    def test_audit_survives_its_target(self):
        """Events outlive the object they describe — that is the point."""
        add_team_member(self.team, self.subject, actor=self.actor)
        team_id = self.team.pk
        self.team.delete()

        self.assertTrue(
            AuditEvent.objects.filter(
                target_type="Team", target_id=str(team_id)
            ).exists()
        )

    def test_audit_trail_reads_back_for_one_object(self):
        add_team_member(self.team, self.subject, actor=self.actor)
        remove_team_member(self.team, self.subject, actor=self.actor)
        self.assertEqual(len(audit_trail(self.team)), 2)

    def test_a_failed_audit_does_not_break_the_action(self):
        """Auditing is best-effort: losing a log line beats failing a grant."""
        from unittest.mock import patch

        with patch.object(
            AuditEvent.objects, "create", side_effect=RuntimeError("db down")
        ):
            membership = add_team_member(self.team, self.subject, actor=self.actor)

        self.assertIsNotNone(membership)
        self.assertTrue(is_team_member(self.subject, self.team))


class BackfillMigrationTests(TestCase):
    """The 0005 backfill must be additive and exactly reversible.

    Exercises the migration's own functions against the live app registry
    rather than re-running the migration, so the logic is covered without
    standing up a second migration executor.
    """

    @staticmethod
    def _migration():
        # Module name starts with a digit, so it cannot be imported by name.
        import importlib

        return importlib.import_module(
            "accounts.migrations.0005_backfill_default_teams"
        )

    def setUp(self):
        from django.apps import apps as global_apps

        self.apps = global_apps
        self.org = Institution.objects.create(name="Org A")
        self.manager = make_user("mgr", UserRole.MANAGER, self.org)
        self.annotator = make_user("ann", UserRole.ANNOTATOR, self.org)
        # Belongs to no organisation — must not be seated anywhere.
        self.unaffiliated = make_user("solo", UserRole.ANNOTATOR, None)
        # The migration already ran during test-DB setup; start from a clean
        # slate so this exercises the functions rather than their leftovers.
        TeamMembership.objects.all().delete()
        Team.objects.all().delete()

    def test_backfill_creates_a_default_team_and_seats_members(self):
        self._migration().create_default_teams(self.apps, None)

        team = Team.objects.get(organization=self.org, is_default=True)
        seated = {m.user_id: m.role for m in TeamMembership.objects.filter(team=team)}
        self.assertEqual(
            seated,
            {
                self.manager.id: TeamRole.MANAGER,
                self.annotator.id: TeamRole.MEMBER,
            },
        )
        self.assertNotIn(self.unaffiliated.id, seated)

    def test_backfill_is_idempotent(self):
        self._migration().create_default_teams(self.apps, None)
        self._migration().create_default_teams(self.apps, None)

        self.assertEqual(Team.objects.filter(organization=self.org).count(), 1)
        self.assertEqual(TeamMembership.objects.count(), 2)

    def test_reverse_removes_exactly_what_forward_created(self):
        self._migration().create_default_teams(self.apps, None)
        self._migration().remove_default_teams(self.apps, None)

        self.assertEqual(Team.objects.count(), 0)
        self.assertEqual(TeamMembership.objects.count(), 0)
        # The rows it was derived from are untouched — this is expand-contract,
        # not a destructive rewrite.
        self.assertEqual(Institution.objects.count(), 1)
        self.assertEqual(UserProfile.objects.count(), 3)

    def test_reverse_leaves_hand_made_teams_alone(self):
        self._migration().create_default_teams(self.apps, None)
        bespoke = Team.objects.create(organization=self.org, name="Mito QC")
        add_team_member(bespoke, self.annotator)

        self._migration().remove_default_teams(self.apps, None)

        self.assertTrue(Team.objects.filter(pk=bespoke.pk).exists())
        self.assertTrue(is_team_member(self.annotator, bespoke))


class FixtureLoadingTests(TestCase):
    """`loaddata` must not collide with the profile-creating signal.

    Regression: ``ensure_user_profile`` fired on the User insert during a raw
    fixture load and won the race against the fixture's own UserProfile row,
    hitting the unique ``user_id`` constraint. That broke every fixture
    containing users — including a dump/restore of a whole database, which is
    exactly what a SQLite→PostgreSQL move needs.
    """

    def test_loaddata_round_trips_users_and_profiles(self):
        import tempfile
        from io import StringIO
        from pathlib import Path

        from django.core.management import call_command

        org = Institution.objects.create(name="Fixture Org")
        user = make_user("fixture-user", UserRole.MANAGER, org)

        out = StringIO()
        call_command(
            "dumpdata", "auth.user", "accounts",
            natural_foreign=True, natural_primary=True, stdout=out,
        )
        payload = out.getvalue()

        # Wipe and reload — the path a database migration actually takes.
        TeamMembership.objects.all().delete()
        Team.objects.all().delete()
        UserProfile.objects.all().delete()
        User.objects.all().delete()
        Institution.objects.all().delete()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.json"
            path.write_text(payload)
            call_command("loaddata", str(path), verbosity=0)

        restored = User.objects.get(username="fixture-user")
        self.assertEqual(restored.profile.role, UserRole.MANAGER)
        self.assertEqual(restored.profile.institution.name, "Fixture Org")
        # Exactly one profile — the signal must not have added a second.
        self.assertEqual(UserProfile.objects.filter(user=restored).count(), 1)

    def test_the_signal_still_creates_a_profile_for_ordinary_users(self):
        """The raw-guard must not disable normal profile creation."""
        user = User.objects.create_user("ordinary", password="pw-for-tests-1")
        self.assertTrue(UserProfile.objects.filter(user=user).exists())

    def test_superusers_still_default_to_manager(self):
        admin = User.objects.create_superuser("root", "a@b.c", "pw-for-tests-1")
        self.assertEqual(admin.profile.role, UserRole.MANAGER)


class WorkingTeamAccessMirrorTests(TestCase):
    """Team eligibility is mirrored into project access, so it must be revocable.

    ``add_team_member`` materialises a ``ProjectMembership`` row for each of the
    team's working projects, which is what ``is_project_member`` reads. Removing
    the person from the team therefore has to remove those mirrored rows —
    otherwise revocation withdraws their tasks but leaves them able to open the
    project, its tasks and its hard cases forever. Access a manager granted by
    hand must survive the same removal.
    """

    def setUp(self):
        self.org = Institution.objects.create(name="Org M")
        self.team = Team.objects.create(organization=self.org, name="Working")
        self.manager = make_user("mgr-mirror", role=UserRole.MANAGER)
        self.annotator = make_user("ann-mirror")
        self.project = Project.objects.create(
            title="Mirrored access", created_by=self.manager
        )
        set_project_working_team(self.project, self.team, actor=self.manager)

    def _membership(self):
        return ProjectMembership.objects.filter(
            project=self.project, user=self.annotator
        ).first()

    def test_joining_the_team_grants_mirrored_project_access(self):
        add_team_member(self.team, self.annotator, actor=self.manager)
        row = self._membership()
        self.assertIsNotNone(row, "joining a working team granted no project access")
        self.assertEqual(row.source, MembershipSource.TEAM)
        self.assertTrue(is_project_member(self.annotator, self.project))

    def test_leaving_the_team_revokes_mirrored_project_access(self):
        add_team_member(self.team, self.annotator, actor=self.manager)
        self.assertTrue(is_project_member(self.annotator, self.project))

        remove_team_member(self.team, self.annotator, actor=self.manager)
        self.assertIsNone(
            self._membership(),
            "mirrored project access outlived the team membership that created it",
        )
        self.assertFalse(is_project_member(self.annotator, self.project))

    def test_hand_granted_access_survives_leaving_the_team(self):
        ensure_project_assignee_eligible(
            self.project, self.annotator, actor=self.manager
        )
        row = self._membership()
        self.assertEqual(
            row.source,
            MembershipSource.EXPLICIT,
            "a deliberate grant was recorded as team-sourced and is revocable",
        )

        remove_team_member(self.team, self.annotator, actor=self.manager)
        self.assertIsNotNone(
            self._membership(), "a hand-granted membership was revoked by team removal"
        )
        self.assertTrue(is_project_member(self.annotator, self.project))

    def test_mirroring_a_team_onto_a_project_does_not_downgrade_a_hand_grant(self):
        ensure_project_assignee_eligible(
            self.project, self.annotator, actor=self.manager
        )
        # Re-applying the working team must not rewrite existing provenance.
        set_project_working_team(self.project, self.team, actor=self.manager)
        self.assertEqual(self._membership().source, MembershipSource.EXPLICIT)
