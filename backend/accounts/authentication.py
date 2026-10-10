"""Resolve a browser workspace without changing an account's durable roles."""
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import PermissionDenied
from .roles import available_roles


class WorkspaceTokenAuthentication(TokenAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        role = request.headers.get("X-Mito-Role")
        if role:
            if role not in available_roles(user):
                raise PermissionDenied("This account no longer has the selected role. Switch to your default workspace.")
            user._active_role = role
        return user, token
