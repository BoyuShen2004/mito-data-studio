"""Submit/approve loop (A–D) + project hard cases (F).

Two things worth noting for anyone reading this later:

* ``HardCaseShare`` is **renamed** to ``HardCase``, not dropped and recreated —
  existing share tokens keep working, which matters because those links may
  already have been pasted somewhere outside the app.
* ``ReviewRecord.submission`` becomes nullable ``SET_NULL`` and gains a durable
  ``task`` FK, because re-submitting now deletes the superseded submission row
  (latest-only, see ``annotation.services._supersede_submissions``) and the
  decision log must outlive it. ``_backfill_review_tasks`` fills ``task`` for
  rows written before this change.
"""

import annotation.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def _backfill_review_tasks(apps, schema_editor):
    """Point pre-existing reviews at their task (via the submission they
    decided). Rows whose submission has already gone are left null — there is
    nothing to recover them from, and null is honest."""
    ReviewRecord = apps.get_model("annotation", "ReviewRecord")
    for review in ReviewRecord.objects.filter(
        task__isnull=True, submission__isnull=False
    ).select_related("submission"):
        review.task_id = review.submission.task_id
        review.save(update_fields=["task"])


def _backfill_hard_case_scope(apps, schema_editor):
    """Denormalize project/volume onto hard cases created before they existed."""
    HardCase = apps.get_model("annotation", "HardCase")
    for case in HardCase.objects.filter(project__isnull=True).select_related("task"):
        case.project_id = case.task.project_id
        case.volume_id = case.task.volume_id
        case.save(update_fields=["project", "volume"])


class Migration(migrations.Migration):

    dependencies = [
        ('annotation', '0005_hardcaseshare'),
        ('projects', '0005_dataset'),
        ('volumes', '0004_backfill_datasets'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- A–D: the submit / approve-and-lock loop ------------------------
        migrations.AddField(
            model_name='annotationtask',
            name='annotation_locked',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='annotationtask',
            name='submission_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='annotationtask',
            name='last_decision',
            field=models.CharField(blank=True, choices=[('approved', 'Approved'), ('rejected', 'Rejected'), ('revision_requested', 'Revision requested')], max_length=20),
        ),
        migrations.AddField(
            model_name='annotationtask',
            name='last_decision_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='annotationtask',
            name='last_decision_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='decided_tasks', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='annotationtask',
            name='last_decision_comments',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='reviewrecord',
            name='task',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='reviews', to='annotation.annotationtask'),
        ),
        migrations.AlterField(
            model_name='reviewrecord',
            name='submission',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviews', to='annotation.annotationsubmission'),
        ),
        migrations.RunPython(_backfill_review_tasks, migrations.RunPython.noop),

        # --- F: HardCaseShare becomes a project-scoped HardCase -------------
        migrations.RenameModel(old_name='HardCaseShare', new_name='HardCase'),
        migrations.AlterField(
            model_name='hardcase',
            name='task',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hard_cases', to='annotation.annotationtask'),
        ),
        migrations.AlterField(
            model_name='hardcase',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hard_cases', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='hardcase',
            name='token',
            field=models.CharField(db_index=True, default=annotation.models._generate_share_token, max_length=64, unique=True),
        ),
        migrations.AddField(
            model_name='hardcase',
            name='project',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='hard_cases', to='projects.project'),
        ),
        migrations.AddField(
            model_name='hardcase',
            name='volume',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='hard_cases', to='volumes.volume'),
        ),
        migrations.AddField(
            model_name='hardcase',
            name='status',
            field=models.CharField(choices=[('open', 'Open'), ('resolved', 'Resolved')], default='open', max_length=20),
        ),
        migrations.AddField(
            model_name='hardcase',
            name='resolved_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resolved_hard_cases', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='hardcase',
            name='resolved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterModelOptions(
            name='hardcase',
            options={'ordering': ['-created_at']},
        ),
        migrations.RunPython(_backfill_hard_case_scope, migrations.RunPython.noop),
    ]
