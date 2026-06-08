from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_clientprofile_is_kcu_student_department"),
    ]

    operations = [
        migrations.AddField(
            model_name="counselorprofile",
            name="birth_date",
            field=models.DateField(blank=True, null=True, verbose_name="생년월일"),
        ),
    ]
