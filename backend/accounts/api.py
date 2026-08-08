"""Authentication + current-user endpoints.

Token auth is used so the React SPA can store the token and avoid CSRF.
"""

from django.contrib.auth import authenticate, get_user_model
from django.conf import settings
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.choices import UserRole
from core.permissions import IsManager

from .models import AnnotatorProfile, AuditEvent
from .roles import get_role, is_annotator, is_requester
from .serializers import (
    CurrentUserSerializer,
    LoginSerializer,
    ProfileUpdateSerializer,
    RegisterSerializer,
)
from .services import people_overview, person_detail, update_own_profile


def _portal_allows(portal: str, user) -> bool:
    """Whether ``user`` may sign in through the given login tab.

    The requester tab is for requesters; the annotator tab is for annotators
    and managers (managers have no separate tab of their own).
    """
    if portal == "requester":
        return is_requester(user)
    if portal == "annotator":
        return is_annotator(user)  # annotators + managers
    return True


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response(
                {"detail": "Invalid username or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        portal = serializer.validated_data.get("portal") or ""
        if portal and not _portal_allows(portal, user):
            label = "Requester" if portal == "requester" else "Annotator"
            return Response(
                {
                    "detail": (
                        f"This account cannot sign in through the {label} "
                        "login. Please use the correct login tab."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {"token": token.key, "user": CurrentUserSerializer(user).data}
        )


class MockLoginView(APIView):
    """Explicitly gated, server-allowlisted passwordless development login."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        if not settings.ENABLE_MOCK_DEV_LOGIN:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        User = get_user_model()
        accounts_by_name = {
            user.username: user
            for user in User.objects.filter(
                username__in=settings.MOCK_DEV_LOGIN_ACCOUNTS, is_active=True
            )
        }
        accounts = [
            accounts_by_name[name]
            for name in settings.MOCK_DEV_LOGIN_ACCOUNTS
            if name in accounts_by_name
        ]
        if not settings.MOCK_DEV_LOGIN_PASSWORD:
            accounts = []
        return Response({
            "enabled": True,
            "accounts": [
                {
                    "username": user.username,
                    "role": get_role(user),
                    "password": settings.MOCK_DEV_LOGIN_PASSWORD,
                }
                for user in accounts
            ],
        })

    def post(self, request):
        if not settings.ENABLE_MOCK_DEV_LOGIN:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "detail": (
                    "Direct development-account login is disabled. Select an "
                    "account, then use the normal Sign in button."
                )
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )


class RegisterView(APIView):
    """Public account creation for annotators and requesters."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {"token": token.key, "user": CurrentUserSerializer(user).data},
            status=status.HTTP_201_CREATED,
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(CurrentUserSerializer(request.user).data)


class PeopleOverviewView(APIView):
    """``GET /api/people/overview/`` — the whole People page in one request.

    Role-scoped server-side (see ``accounts.services.people_overview``): the
    payload shape is identical for every role, so the client renders panels by
    which lists are non-empty rather than re-deriving who-works-with-whom from
    a fan-out of list endpoints.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(people_overview(request.user))


class MyProfileView(APIView):
    """``PATCH /api/people/me/`` — edit your own short profile (display name,
    lab/institution, contact note). Returns the refreshed current-user
    payload so the client can update its auth context in place."""

    permission_classes = [IsAuthenticated]

    def patch(self, request):
        serializer = ProfileUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            update_own_profile(request.user, serializer.validated_data)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        request.user.refresh_from_db()
        return Response(CurrentUserSerializer(request.user).data)


class PersonDetailView(APIView):
    """``GET /api/people/<username>/`` — read-only card for one person."""

    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        card = person_detail(request.user, username)
        if card is None:
            return Response({"detail": "No such person."}, status=status.HTTP_404_NOT_FOUND)
        return Response(card)


class AnnotatorListView(APIView):
    """List annotators for manager assignment dropdowns. Managers only."""

    permission_classes = [IsManager]

    def get(self, request):
        profiles = (
            AnnotatorProfile.objects.select_related("user")
            .filter(user__is_active=True)
            .order_by("user__username")
        )
        data = [
            {
                "id": p.user_id,
                "username": p.user.get_username(),
                "is_active_annotator": p.is_active_annotator,
                "max_active_tasks": p.max_active_tasks,
            }
            for p in profiles
            if get_role(p.user) in (UserRole.ANNOTATOR,)
        ]
        return Response(data)
