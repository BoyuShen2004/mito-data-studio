from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0009_userprofile_annotate_shortcuts")]

    operations = [
        migrations.AlterField(
            model_name="auditevent",
            name="verb",
            field=models.CharField(
                choices=[
                    ("team.member_added", "Team member added"),
                    ("team.member_removed", "Team member removed"),
                    ("team.role_changed", "Team role changed"),
                    ("project.team_granted", "Project access granted to team"),
                    ("project.team_revoked", "Project access revoked from team"),
                    ("experience.set", "Experience set"),
                    ("experience.cleared", "Experience cleared"),
                    ("permission.denied", "Permission denied"),
                    ("task.claimed", "Task instance claimed"),
                    ("task.assigned", "Task instance assigned manually"),
                    ("task.transferred", "Task instance transferred"),
                    ("task.released", "Task instance released"),
                    ("task.lease_expired", "Task instance lease expired"),
                    ("submission.created", "Submission recorded"),
                    ("submission.superseded", "Submission superseded"),
                    ("review.recorded", "Review decision recorded"),
                    ("task.labels_reset", "Task working labels reset"),
                ],
                max_length=64,
            ),
        ),
    ]
