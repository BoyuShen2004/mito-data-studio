from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("annotation", "0014_revert_to_single_assignee")]

    operations = [
        migrations.RemoveField(model_name="annotationoperation", name="instance"),
        migrations.RemoveField(model_name="worksession", name="instance"),
        migrations.RemoveConstraint(
            model_name="annotationtask",
            name="pending_instances_non_negative",
        ),
        migrations.RemoveField(model_name="annotationtask", name="task_type_ref"),
        migrations.RemoveField(model_name="annotationtask", name="total_instances"),
        migrations.RemoveField(model_name="annotationtask", name="pending_instances"),
        migrations.DeleteModel(name="TaskInstance"),
        migrations.DeleteModel(name="TaskType"),
    ]
