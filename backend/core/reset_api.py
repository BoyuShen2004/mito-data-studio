from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from core.application_reset import (
    CONFIRM_PHRASE, DEVELOPMENT_CONFIRM_PHRASE, ResetRefused,
    assert_reset_environment, backup_status, execute_development_reset,
    execute_reset, issue_confirmation, row_summary, storage_manifest,
)
from core.deployment import identity


class IsSuperuser(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ResetStatusView(APIView):
    permission_classes = [IsSuperuser]

    def get(self, request):
        return Response({
            "phrase": CONFIRM_PHRASE,
            "identity": identity(),
            "backup": backup_status(),
            "maintenance": settings.MITO_MAINTENANCE_MODE,
            "clear": row_summary(),
            "storage": storage_manifest(),
            "retain": ["schema migrations", "designated administrator", "deployment configuration", "external source/reference bytes", "models and release assets"],
        })


@method_decorator(csrf_protect, name="dispatch")
class ResetConfirmView(APIView):
    permission_classes = [IsSuperuser]

    def post(self, request):
        if not request.user.check_password(str(request.data.get("password", ""))):
            return Response({"detail": "Current password is incorrect."}, status=status.HTTP_403_FORBIDDEN)
        try:
            raw = issue_confirmation(request.user, str(request.data.get("phrase", "")))
        except ResetRefused as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({"confirmation_token": raw, "expires_in_seconds": 300, "identity": identity()})


@method_decorator(csrf_protect, name="dispatch")
class ResetExecuteView(APIView):
    permission_classes = [IsSuperuser]

    def post(self, request):
        try:
            assert_reset_environment()
            result = execute_reset(
                request.user, str(request.data.get("confirmation_token", "")),
                str(request.data.get("phrase", "")),
            )
        except ResetRefused as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(result)


@method_decorator(ensure_csrf_cookie, name="dispatch")
@method_decorator(csrf_protect, name="dispatch")
class DevelopmentResetView(APIView):
    """One-confirmation reset for explicitly disposable demo/dev installs."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @staticmethod
    def _enabled() -> bool:
        return bool(
            settings.ENABLE_MOCK_DEV_LOGIN
            and settings.MITO_ALLOW_DEV_RESET
            and settings.MOCK_DEV_LOGIN_ACCOUNTS
        )

    def get(self, request):
        if not self._enabled():
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            "enabled": True,
            "confirmation": DEVELOPMENT_CONFIRM_PHRASE,
            "clear": row_summary(),
        })

    def post(self, request):
        if not self._enabled():
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            result = execute_development_reset(
                str(request.data.get("confirmation", ""))
            )
        except ResetRefused as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(result)
