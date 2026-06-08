from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_counselorprofile_birth_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="counselorprofile",
            name="gender",
            field=models.CharField(blank=True, max_length=10, verbose_name="성별"),
        ),
    ]
