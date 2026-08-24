from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("annotation", "0023_reviewlabelcomment"),
    ]

    operations = [
        migrations.AddField(
            model_name="hardcase",
            name="view_axis",
            field=models.CharField(blank=True, default="", max_length=1),
        ),
        migrations.AddField(
            model_name="hardcase",
            name="view_x",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hardcase",
            name="view_y",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hardcase",
            name="view_z",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="reviewlabelcomment",
            name="view_axis",
            field=models.CharField(blank=True, default="", max_length=1),
        ),
        migrations.AddField(
            model_name="reviewlabelcomment",
            name="view_x",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="reviewlabelcomment",
            name="view_y",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="reviewlabelcomment",
            name="view_z",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
