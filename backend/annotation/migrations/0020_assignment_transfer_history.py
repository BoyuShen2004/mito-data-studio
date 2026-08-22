from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("annotation", "0019_hardcasemessage"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="assignmentwithdrawal",
            name="outcome",
            field=models.CharField(
                choices=[("withdrawn", "Withdrawn"), ("transferred", "Transferred")],
                default="withdrawn",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="assignmentwithdrawal",
            name="transferred_to",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assignment_transfers_received",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
