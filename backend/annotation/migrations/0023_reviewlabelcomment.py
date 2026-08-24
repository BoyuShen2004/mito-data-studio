from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("annotation", "0022_hardcase_stable_ordering"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReviewLabelComment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label_id", models.PositiveIntegerField()),
                ("body", models.TextField(max_length=1000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="review_label_comments", to=settings.AUTH_USER_MODEL)),
                ("submission", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="label_comments", to="annotation.annotationsubmission")),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="review_label_comments", to="annotation.annotationtask")),
            ],
            options={"ordering": ["-updated_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="reviewlabelcomment",
            constraint=models.UniqueConstraint(fields=("submission", "label_id"), name="unique_review_comment_per_submission_label"),
        ),
        migrations.AddIndex(
            model_name="reviewlabelcomment",
            index=models.Index(fields=["task", "-updated_at"], name="idx_review_comment_task_time"),
        ),
    ]
