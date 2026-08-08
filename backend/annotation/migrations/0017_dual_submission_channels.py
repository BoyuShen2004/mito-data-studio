from django.db import migrations, models


def backfill_submission_channels(apps, schema_editor):
    Submission = apps.get_model("annotation", "AnnotationSubmission")
    Review = apps.get_model("annotation", "ReviewRecord")
    Task = apps.get_model("annotation", "AnnotationTask")

    for submission in Submission.objects.all().iterator():
        reviews = Review.objects.filter(submission_id=submission.pk).order_by("reviewed_at", "id")
        latest = reviews.last()
        reviews.update(source=submission.source)
        if latest is not None:
            state = latest.decision
        elif submission.superseded_at is not None:
            state = "voided"
        else:
            state = "pending"
        Submission.objects.filter(pk=submission.pk).update(review_status=state)

    for task in Task.objects.all().iterator():
        latest = Review.objects.filter(task_id=task.pk).order_by("reviewed_at", "id").last()
        if latest is not None:
            Task.objects.filter(pk=task.pk).update(last_decision_source=latest.source)


class Migration(migrations.Migration):
    dependencies = [("annotation", "0016_assignmentwithdrawal")]

    operations = [
        migrations.AddField(
            model_name="annotationtask",
            name="last_decision_source",
            field=models.CharField(
                blank=True,
                choices=[("upload", "Uploaded file"), ("inapp", "In-app editor")],
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="annotationsubmission",
            name="review_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("revision_requested", "Revision requested"),
                    ("voided", "Voided"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="annotationsubmission",
            name="superseded_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="reviewrecord",
            name="source",
            field=models.CharField(
                blank=True,
                choices=[("upload", "Uploaded file"), ("inapp", "In-app editor")],
                max_length=10,
            ),
        ),
        migrations.AddIndex(
            model_name="annotationsubmission",
            index=models.Index(
                fields=["task", "source", "review_status"],
                name="idx_sub_task_source_state",
            ),
        ),
        migrations.RunPython(backfill_submission_channels, migrations.RunPython.noop),
    ]
