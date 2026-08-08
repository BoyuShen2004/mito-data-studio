from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("annotation", "0015_remove_retired_claim_hierarchy"),
        ("projects", "0011_project_working_team"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AssignmentWithdrawal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("team_name", models.CharField(max_length=255)),
                ("reason", models.CharField(default="Assignment withdrawn", max_length=255)),
                ("withdrawn_at", models.DateTimeField(auto_now_add=True)),
                ("annotator", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="assignment_withdrawals", to=settings.AUTH_USER_MODEL)),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="withdrawals", to="annotation.annotationtask")),
            ],
            options={"ordering": ["-withdrawn_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="assignmentwithdrawal",
            index=models.Index(fields=["annotator", "-withdrawn_at"], name="idx_withdrawal_annotator"),
        ),
    ]
