from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("accounts", "0007_alter_auditevent_verb_phase5")]

    operations = [migrations.DeleteModel(name="Experience")]
