from django.db import migrations, models
import django.db.models.deletion


def backfill_working_team(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    for project in Project.objects.all().iterator():
        team_id = project.teams.order_by("id").values_list("id", flat=True).first()
        if team_id:
            project.working_team_id = team_id
            project.save(update_fields=["working_team"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0008_remove_retired_experience"),
        ("projects", "0010_publicshare"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="working_team",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="working_projects",
                to="accounts.team",
            ),
        ),
        migrations.RunPython(backfill_working_team, migrations.RunPython.noop),
    ]
