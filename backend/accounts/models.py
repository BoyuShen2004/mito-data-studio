from django.conf import settings
from django.db import models

from core.choices import AuditVerb, TeamRole, UserRole


class Institution(models.Model):
    """An organisation that owns or requests annotation projects.

    Phase 1 note: this **is** the ``Organization`` of the target domain model
    (research doc `16`). Rather than stand up a parallel table and migrate rows
    between them, Teams hang off this one and the rename is deferred to the
    contract step — so no data moves and no FK is repointed.
    """

    name = models.CharField(max_length=255, unique=True)
    institution_type = models.CharField(max_length=100, blank=True)
    contact_email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class UserProfile(models.Model):
    """Role and institution attached to a Django ``User``."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(
        max_length=20, choices=UserRole.choices, default=UserRole.ANNOTATOR
    )
    institution = models.ForeignKey(
        Institution,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="members",
    )
    institution_name = models.CharField(max_length=255, blank=True)
    # The short, self-editable profile the People surface shows for every
    # role (see ``accounts.services.people_overview``). Kept here rather than
    # in a parallel profile model: role + institution already live on this
    # row, and a person has exactly one of these regardless of role.
    display_name = models.CharField(max_length=150, blank=True)
    contact_note = models.CharField(max_length=280, blank=True)
    # Per-user annotate tool shortcuts: ``{tool: letter}``, held with Cmd on
    # macOS and Ctrl elsewhere. Server-side so the bindings follow the account
    # between browsers and machines, which localStorage could not do. Empty
    # means "never customised", i.e. use the defaults — see
    # ``accounts/shortcuts.py`` for the shape and why the modifier is not part
    # of it.
    annotate_shortcuts = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.user.get_username()} ({self.get_role_display()})"

    @property
    def label(self) -> str:
        """What to call this person in the UI — their display name if set."""
        return self.display_name or self.user.get_username()

    @property
    def is_manager(self) -> bool:
        return self.role == UserRole.MANAGER

    @property
    def is_annotator(self) -> bool:
        return self.role == UserRole.ANNOTATOR


class AnnotatorProfile(models.Model):
    """Annotation-specific capacity and quality info.

    Annotation work is unpaid; no wage/pay-rate fields are tracked.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="annotator_profile",
    )
    is_active_annotator = models.BooleanField(default=True)
    max_active_tasks = models.PositiveIntegerField(default=5)
    quality_score = models.FloatField(default=0.0)
    notes = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"Annotator: {self.user.get_username()}"


# --- Teams and access audit -------------------------------------------------
# Additive by design (expand-contract): nothing above is modified or dropped,
# and every read path stays behind `settings.FEATURE_TEAMS` until backfilled.
# See docs/webknossos-transformation/16-target-domain-model.md.


class Team(models.Model):
    """A working group inside an organisation.

    Teams are the unit permissions and task eligibility are granted to — the
    piece mito was missing. Project access becomes "which teams may work on
    this", instead of the ad-hoc "created it, or holds a task on it" rule in
    ``annotation.services.is_project_member``.
    """

    organization = models.ForeignKey(
        Institution, on_delete=models.CASCADE, related_name="teams"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    # Every organisation gets one default team so a user always has somewhere
    # to belong; mirrors WEBKNOSSOS's per-organisation default team.
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["organization_id", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"], name="uniq_team_name_per_org"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.organization.name})"


class TeamMembership(models.Model):
    """A user's membership of one team, with their role *in that team*."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="team_memberships",
    )
    role = models.CharField(
        max_length=20, choices=TeamRole.choices, default=TeamRole.MEMBER
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["team_id", "user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "user"], name="uniq_membership_per_team_user"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user.get_username()}@{self.team.name} ({self.role})"

    @property
    def is_manager(self) -> bool:
        return self.role == TeamRole.MANAGER


class AuditEvent(models.Model):
    """Append-only record of a permission-relevant action.

    Never updated and never deleted in normal operation — the point is that
    "who granted whom access, and when" survives the state it describes. Target
    is stored as (type, id) rather than a FK so an event outlives its subject.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    verb = models.CharField(max_length=64, choices=AuditVerb.choices)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    # Free-form context: which role, which project, why a check failed.
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["verb", "-created_at"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self) -> str:
        who = self.actor.get_username() if self.actor else "system"
        return f"{who} {self.verb} {self.target_type}#{self.target_id}"
