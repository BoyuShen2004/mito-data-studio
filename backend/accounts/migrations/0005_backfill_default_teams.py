"""Phase 1 backfill: give every organisation a default team and seat its members.

The *expand* step of expand-contract. Purely additive — no existing row is
modified and no column is dropped — and fully reversible: the reverse function
removes exactly what the forward one created (default teams and their
memberships), leaving any hand-made team untouched.

Safe to apply well ahead of `FEATURE_TEAMS`. Seating users in teams changes no
access decision on its own, because access also requires a project to have been
*granted* a team, and this migration grants none.
"""

from django.db import migrations

TEAM_ROLE_MEMBER = "member"
TEAM_ROLE_MANAGER = "manager"
USER_ROLE_MANAGER = "manager"


def create_default_teams(apps, schema_editor):
    Institution = apps.get_model("accounts", "Institution")
    UserProfile = apps.get_model("accounts", "UserProfile")
    Team = apps.get_model("accounts", "Team")
    TeamMembership = apps.get_model("accounts", "TeamMembership")

    for org in Institution.objects.all():
        team, _ = Team.objects.get_or_create(
            organization=org,
            name="Default",
            defaults={
                "is_default": True,
                "description": "Created automatically for existing members.",
            },
        )

        profiles = UserProfile.objects.filter(institution=org).select_related("user")
        for profile in profiles:
            # An org manager becomes a team manager; everyone else a member.
            role = (
                TEAM_ROLE_MANAGER
                if profile.role == USER_ROLE_MANAGER
                else TEAM_ROLE_MEMBER
            )
            TeamMembership.objects.get_or_create(
                team=team, user_id=profile.user_id, defaults={"role": role}
            )


def remove_default_teams(apps, schema_editor):
    """Undo exactly what the forward migration created."""
    Team = apps.get_model("accounts", "Team")
    TeamMembership = apps.get_model("accounts", "TeamMembership")

    default_teams = Team.objects.filter(is_default=True, name="Default")
    TeamMembership.objects.filter(team__in=default_teams).delete()
    default_teams.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_team_teammembership_auditevent_experience_and_more"),
    ]

    operations = [
        migrations.RunPython(create_default_teams, remove_default_teams),
    ]
