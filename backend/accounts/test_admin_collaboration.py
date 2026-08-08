"""Manager Admin coverage for the integrated organization/team surfaces."""

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.choices import AuditVerb, TeamRole, UserRole
from projects.models import Project

from .models import (
    AuditEvent,
    Institution,
    Team,
    TeamMembership,
    UserProfile,
)


class CollaborationAdminTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            "collaboration_manager",
            password="test-only-password",
            is_staff=True,
        )
        UserProfile.objects.update_or_create(
            user=self.manager,
            defaults={"role": UserRole.MANAGER},
        )
        # The post-save signal populated the related-profile cache when the
        # user was created; reload so direct ModelAdmin permission checks see
        # the persisted manager role, just like a real request does.
        self.manager.refresh_from_db()
        self.annotator = User.objects.create_user("collaboration_annotator")
        self.organization = Institution.objects.create(name="Release Org")
        self.team = Team.objects.create(
            organization=self.organization,
            name="Release Team",
        )
        self.membership = TeamMembership.objects.create(
            team=self.team,
            user=self.annotator,
        )
        self.event = AuditEvent.objects.create(
            actor=self.manager,
            verb=AuditVerb.TEAM_MEMBER_ADDED,
            target_type="team_membership",
            target_id=str(self.membership.pk),
            metadata={"release_test": True},
        )

    def test_all_collaboration_models_have_manager_admin_surfaces(self):
        self.client.force_login(self.manager)
        for model in (Institution, Team, TeamMembership, AuditEvent):
            with self.subTest(model=model._meta.label):
                response = self.client.get(
                    reverse(
                        f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist"
                    )
                )
                self.assertEqual(response.status_code, 200)

    def test_team_membership_is_manager_editable(self):
        request = RequestFactory().get("/admin/")
        request.user = self.manager
        for model, obj in (
            (Team, self.team),
            (TeamMembership, self.membership),
        ):
            with self.subTest(model=model._meta.label):
                model_admin = admin.site._registry[model]
                self.assertTrue(model_admin.has_add_permission(request))
                self.assertTrue(model_admin.has_change_permission(request, obj))
                self.assertFalse(model_admin.has_delete_permission(request, obj))

    def test_audit_events_are_visible_but_append_only(self):
        request = RequestFactory().get("/admin/")
        request.user = self.manager
        model_admin = admin.site._registry[AuditEvent]
        self.assertTrue(model_admin.has_view_permission(request, self.event))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request, self.event))
        self.assertFalse(model_admin.has_delete_permission(request, self.event))

    def test_admin_membership_changes_are_audited(self):
        request = RequestFactory().post("/admin/")
        request.user = self.manager

        membership_admin = admin.site._registry[TeamMembership]
        self.membership.role = TeamRole.MANAGER
        membership_admin.save_model(request, self.membership, form=None, change=True)
        role_event = AuditEvent.objects.filter(
            verb=AuditVerb.TEAM_ROLE_CHANGED,
            target_type="Team",
            target_id=str(self.team.pk),
        ).latest("id")
        self.assertEqual(role_event.actor, self.manager)
        self.assertEqual(role_event.metadata["previous_role"], TeamRole.MEMBER)
        self.assertEqual(role_event.metadata["role"], TeamRole.MANAGER)

    def test_project_team_grants_and_revocations_are_audited(self):
        project = Project.objects.create(title="Release Project", created_by=self.manager)
        project_admin = admin.site._registry[Project]
        request = RequestFactory().post("/admin/")
        request.user = self.manager

        class TeamForm:
            instance = project

            def __init__(self, teams):
                self.teams = teams

            def save_m2m(self):
                self.instance.teams.set(self.teams)

        project_admin.save_model(request, project, form=None, change=True)
        project_admin.save_related(request, TeamForm([self.team]), [], change=True)
        self.assertTrue(
            AuditEvent.objects.filter(
                verb=AuditVerb.PROJECT_TEAM_GRANTED,
                target_type="Project",
                target_id=str(project.pk),
                actor=self.manager,
            ).exists()
        )

        self.assertEqual(set(project.teams.values_list("id", flat=True)), {self.team.id})
        project_admin.save_model(request, project, form=None, change=True)
        self.assertEqual(project._mito_team_ids_before, {self.team.id})  # noqa: SLF001
        project_admin.save_related(request, TeamForm([]), [], change=True)
        revoke_events = AuditEvent.objects.filter(
                verb=AuditVerb.PROJECT_TEAM_REVOKED,
                target_type="Project",
                target_id=str(project.pk),
                actor=self.manager,
            )
        self.assertTrue(
            revoke_events.exists(),
            list(AuditEvent.objects.values("verb", "target_type", "target_id", "metadata")),
        )
