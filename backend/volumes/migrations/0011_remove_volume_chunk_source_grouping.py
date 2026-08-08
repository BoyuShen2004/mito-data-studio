from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("volumes", "0010_alter_volume_status"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="volume",
            name="chunk_id",
        ),
        migrations.RemoveField(
            model_name="volume",
            name="source_volume",
        ),
    ]
