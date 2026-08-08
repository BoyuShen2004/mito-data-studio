from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0009_project_membership"),
        ("volumes", "0007_volume_file_format_nifti"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="PublicShare",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.CharField(editable=False, max_length=64, unique=True)),
                ("scope", models.CharField(choices=[("project", "Project"), ("dataset", "Dataset"), ("volume", "Volume")], max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_public_shares", to=settings.AUTH_USER_MODEL)),
                ("dataset", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="public_shares", to="projects.dataset")),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="public_shares", to="projects.project")),
                ("revoked_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="revoked_public_shares", to=settings.AUTH_USER_MODEL)),
                ("volume", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="public_shares", to="volumes.volume")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
