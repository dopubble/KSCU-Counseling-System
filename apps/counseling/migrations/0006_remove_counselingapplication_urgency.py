from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0005_case_day_of_cancel_count"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="counselingapplication",
            name="urgency",
        ),
    ]
