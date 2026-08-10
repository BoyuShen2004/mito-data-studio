from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("annotation", "0018_hardcase_note"),
    ]

    operations = [
        migrations.CreateModel(
            name="HardCaseMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("body", models.TextField(max_length=2000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hard_case_messages", to=settings.AUTH_USER_MODEL)),
                ("hard_case", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="annotation.hardcase")),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
    ]
