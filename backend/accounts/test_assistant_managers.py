"""Dual-role authorization: durable identity, request workspace and revocation."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from accounts.models import AuditEvent
from accounts.roles import get_role
from core.choices import UserRole


class AssistantManagerTests(TestCase):
    def setUp(self):
        self.manager = self.user('primary', UserRole.MANAGER)
        self.alice = self.user('dual', UserRole.ANNOTATOR)
        self.plain = self.user('plain', UserRole.ANNOTATOR)
        self.requester = self.user('requester', UserRole.REQUESTER)

    def user(self, name, role):
        user = get_user_model().objects.create_user(name, password='test-password-123')
        user.profile.role = role
        user.profile.save()
        return user

    def authed(self, user, role=None):
        client = APIClient()
        headers = {'HTTP_AUTHORIZATION': 'Token ' + Token.objects.get_or_create(user=user)[0].key}
        if role: headers['HTTP_X_MITO_ROLE'] = role
        client.credentials(**headers)
        return client

    def grant(self, enabled=True):
        return self.authed(self.manager).patch(f'/api/people/{self.alice.pk}/assistant-manager/', {'enabled': enabled}, format='json')

    def test_grant_preserves_annotator_identity_and_audits_changes(self):
        self.assertEqual(self.grant().status_code, 200)
        self.alice.refresh_from_db()
        self.assertEqual(get_role(self.alice), 'annotator')
        data = self.authed(self.alice).get('/api/auth/me/').data
        self.assertEqual(data['available_roles'], ['annotator', 'manager'])
        self.assertTrue(data['is_assistant_manager'])
        self.assertFalse(data['can_manage_assistant_managers'])
        self.grant()
        self.assertEqual(AuditEvent.objects.filter(verb='account.assistant_manager_changed').count(), 1)

    def test_workspace_changes_actual_manager_authorization(self):
        self.grant()
        self.assertEqual(self.authed(self.alice).get('/api/annotators/').status_code, 403)
        manager_client = self.authed(self.alice, 'manager')
        self.assertEqual(manager_client.get('/api/annotators/').status_code, 200)
        self.assertEqual(manager_client.get('/api/auth/me/').data['role'], 'manager')
        self.assertEqual(self.authed(self.alice, 'annotator').get('/api/annotators/').status_code, 403)
        # Role choice is request-local: no other browser/account is changed.
        self.assertEqual(self.authed(self.alice).get('/api/auth/me/').data['role'], 'annotator')

    def test_forged_roles_and_grant_chaining_are_denied(self):
        self.assertEqual(self.authed(self.plain, 'manager').get('/api/auth/me/').status_code, 403)
        self.grant()
        response = self.authed(self.alice, 'manager').patch(f'/api/people/{self.plain.pk}/assistant-manager/', {'enabled': True}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.authed(self.requester).patch(f'/api/people/{self.plain.pk}/assistant-manager/', {'enabled': True}, format='json').status_code, 403)

    def test_revoke_invalidates_manager_requests_but_keeps_identity(self):
        self.grant()
        manager_client = self.authed(self.alice, 'manager')
        self.grant(False)
        self.assertEqual(manager_client.get('/api/annotators/').status_code, 403)
        self.assertEqual(self.authed(self.alice).get('/api/auth/me/').data['available_roles'], ['annotator'])
        self.assertEqual(AuditEvent.objects.filter(verb='account.assistant_manager_changed').count(), 2)

    def test_dual_identity_appears_in_both_rosters(self):
        self.grant()
        data = self.authed(self.manager).get('/api/people/overview/').data
        self.assertIn(self.alice.pk, [p['id'] for p in data['annotators']])
        self.assertEqual([p['id'] for p in data['assistant_managers']], [self.alice.pk])
        self.assertEqual(self.authed(self.alice).get('/api/people/overview/').data['assistant_managers'], [])

    def test_invalid_target_and_value_do_not_grant_access(self):
        primary = self.authed(self.manager)
        self.assertEqual(primary.patch(f'/api/people/{self.requester.pk}/assistant-manager/', {'enabled': True}, format='json').status_code, 404)
        self.assertEqual(primary.patch(f'/api/people/{self.alice.pk}/assistant-manager/', {'enabled': 'true'}, format='json').status_code, 400)
        self.assertEqual(primary.patch(f'/api/people/{self.manager.pk}/assistant-manager/', {'enabled': True}, format='json').status_code, 404)

    def test_switch_endpoint_checks_available_roles(self):
        self.assertEqual(self.authed(self.alice).post('/api/auth/role/', {'role': 'manager'}, format='json').status_code, 403)
        self.grant()
        data = self.authed(self.alice).post('/api/auth/role/', {'role': 'manager'}, format='json')
        self.assertEqual(data.status_code, 200)
        self.assertEqual(data.data['role'], 'manager')
        self.assertEqual(self.authed(self.alice).post('/api/auth/role/', {'role': 'requester'}, format='json').status_code, 403)

    def test_self_profile_patch_cannot_grant_roles(self):
        self.authed(self.alice).patch('/api/people/me/', {'is_assistant_manager': True, 'role': 'manager'}, format='json')
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.profile.is_assistant_manager)

    def test_project_scope_switches_with_workspace(self):
        from projects.services import create_project
        project = create_project(title='Manager-only context', created_by=self.manager)
        self.grant()
        self.assertNotIn(project.id, [p['id'] for p in self.authed(self.alice).get('/api/projects/').data])
        self.assertIn(project.id, [p['id'] for p in self.authed(self.alice, 'manager').get('/api/projects/').data])
        self.assertNotIn(project.id, [p['id'] for p in self.authed(self.alice, 'annotator').get('/api/projects/').data])
