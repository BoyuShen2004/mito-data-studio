from django.db import migrations, models


def clamp_levels(apps, schema_editor):
    """Snap pre-existing integer priorities/difficulties onto the 1–5 scale.

    Old rows used priority default 0 and free-form integers; the levelled fields
    expect 1 (Lowest / Very easy) … 5 (Urgent / Very hard). Anything outside that
    range is mapped to the middle level (Normal / Moderate = 3)."""
    Task = apps.get_model("annotation", "AnnotationTask")
    for task in Task.objects.all():
        changed = []
        if task.priority not in (1, 2, 3, 4, 5):
            task.priority = 3
            changed.append("priority")
        if task.difficulty not in (1, 2, 3, 4, 5):
            task.difficulty = 3
            changed.append("difficulty")
        if changed:
            task.save(update_fields=changed)


class Migration(migrations.Migration):

    dependencies = [
        ("annotation", "0002_remove_annotationtask_payment_amount"),
    ]

    operations = [
        migrations.AlterField(
            model_name="annotationtask",
            name="priority",
            field=models.IntegerField(
                choices=[
                    (1, "Lowest"),
                    (2, "Low"),
                    (3, "Normal"),
                    (4, "High"),
                    (5, "Urgent"),
                ],
                default=3,
            ),
        ),
        migrations.AlterField(
            model_name="annotationtask",
            name="difficulty",
            field=models.IntegerField(
                choices=[
                    (1, "Very easy"),
                    (2, "Easy"),
                    (3, "Moderate"),
                    (4, "Hard"),
                    (5, "Very hard"),
                ],
                default=3,
            ),
        ),
        migrations.RunPython(clamp_levels, migrations.RunPython.noop),
    ]
