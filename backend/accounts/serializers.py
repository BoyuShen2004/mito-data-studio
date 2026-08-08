from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from core.choices import UserRole

from .models import AnnotatorProfile, UserProfile
from .roles import get_role
from .shortcuts import (
    ANNOTATE_SHORTCUT_TOOLS,
    DEFAULT_ANNOTATE_SHORTCUTS,
    effective_annotate_shortcuts,
    may_customize_annotate_shortcuts,
    normalize_annotate_shortcuts,
)

User = get_user_model()


class CurrentUserSerializer(serializers.Serializer):
    """Serialized representation of the authenticated user for the frontend."""

    id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    is_superuser = serializers.BooleanField()
    role = serializers.SerializerMethodField()
    institution_name = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()
    contact_note = serializers.SerializerMethodField()
    # Everything the editor needs to bind (and the profile page needs to edit)
    # per-user tool shortcuts, on the payload the client already loads at login
    # — a separate request would leave a window where the editor is mounted
    # with no bindings.
    annotate_shortcuts = serializers.SerializerMethodField()
    annotate_shortcut_defaults = serializers.SerializerMethodField()
    annotate_shortcut_tools = serializers.SerializerMethodField()
    can_customize_shortcuts = serializers.SerializerMethodField()

    def get_role(self, user):
        return get_role(user)

    def get_annotate_shortcuts(self, user):
        profile: UserProfile | None = getattr(user, "profile", None)
        return effective_annotate_shortcuts(profile.annotate_shortcuts if profile else None)

    def get_annotate_shortcut_defaults(self, _user):
        return dict(DEFAULT_ANNOTATE_SHORTCUTS)

    def get_annotate_shortcut_tools(self, _user):
        return [{"tool": tool, "label": label} for tool, label in ANNOTATE_SHORTCUT_TOOLS]

    def get_can_customize_shortcuts(self, user):
        return may_customize_annotate_shortcuts(get_role(user))

    def get_display_name(self, user):
        profile: UserProfile | None = getattr(user, "profile", None)
        return (profile.display_name if profile else "") or ""

    def get_contact_note(self, user):
        profile: UserProfile | None = getattr(user, "profile", None)
        return (profile.contact_note if profile else "") or ""

    def get_institution_name(self, user):
        profile: UserProfile | None = getattr(user, "profile", None)
        if profile is None:
            return ""
        return profile.institution_name or (
            profile.institution.name if profile.institution else ""
        )


class ProfileUpdateSerializer(serializers.Serializer):
    """The short, self-editable profile behind ``PATCH /api/people/me/``.

    Only the fields in ``accounts.services.EDITABLE_PROFILE_FIELDS`` — role and
    the institution *link* are administrative and stay out of reach here.
    """

    display_name = serializers.CharField(
        required=False, allow_blank=True, max_length=150
    )
    institution_name = serializers.CharField(
        required=False, allow_blank=True, max_length=255
    )
    contact_note = serializers.CharField(
        required=False, allow_blank=True, max_length=280
    )
    annotate_shortcuts = serializers.DictField(
        required=False, child=serializers.CharField(allow_blank=True, max_length=8)
    )

    def validate_annotate_shortcuts(self, value):
        # Conflicts and bad letters are rejected here rather than stored: a
        # binding that cannot fire is worse than no binding, because the person
        # who set it believes it works.
        try:
            return normalize_annotate_shortcuts(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(style={"input_type": "password"})
    # Which login tab was used: "requester" or "annotator". Managers use the
    # annotator tab. Optional; when provided the role is validated against it.
    portal = serializers.ChoiceField(
        choices=["requester", "annotator"], required=False, allow_blank=True
    )


# Roles a member of the public may self-register as. Managers are provisioned
# by administrators only; there is no public manager registration.
PUBLIC_ROLES = (UserRole.ANNOTATOR, UserRole.REQUESTER)


class RegisterSerializer(serializers.Serializer):
    """Public account creation for annotators and requesters."""

    username = serializers.CharField(max_length=150)
    password = serializers.CharField(
        style={"input_type": "password"}, write_only=True
    )
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(choices=[r.value for r in PUBLIC_ROLES])
    institution_name = serializers.CharField(
        required=False, allow_blank=True, default=""
    )

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("That username is already taken.")
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data["username"],
            email=validated_data.get("email", ""),
            password=validated_data["password"],
        )
        # ``ensure_user_profile`` created a default profile via post_save; set
        # the chosen role on that same (cached) instance so ``user.profile``
        # reflects the update immediately.
        profile = getattr(user, "profile", None) or UserProfile.objects.create(
            user=user
        )
        profile.role = validated_data["role"]
        profile.institution_name = validated_data.get("institution_name", "")
        profile.save(update_fields=["role", "institution_name"])

        if validated_data["role"] == UserRole.ANNOTATOR:
            AnnotatorProfile.objects.get_or_create(user=user)
        return user
