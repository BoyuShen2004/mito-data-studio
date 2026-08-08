"""DRF permission classes keyed off the user's role.

Role is resolved via :mod:`accounts.roles`, which treats superusers as
managers so an admin-created superuser can drive the full workflow.
"""

from rest_framework.permissions import BasePermission, SAFE_METHODS

from accounts.roles import can_register_data, is_annotator, is_manager, is_requester


class IsManager(BasePermission):
    message = "Manager access required."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated) and is_manager(
            request.user
        )


class IsAnnotator(BasePermission):
    message = "Annotator access required."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated) and is_annotator(
            request.user
        )


class IsRequester(BasePermission):
    message = "Requester access required."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated) and is_requester(
            request.user
        )


class CanRegisterData(BasePermission):
    """Requesters and managers may register datasets and view their projects."""

    message = "Requester or manager access required."

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated
        ) and can_register_data(request.user)


class CanViewProjectData(BasePermission):
    """All signed-in participants may read shared project/data chrome.

    Mutations retain the existing requester/manager registration boundary;
    object querysets still enforce which projects an annotator participates in.
    """

    message = "Project participant access required."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.method in SAFE_METHODS or can_register_data(request.user)
