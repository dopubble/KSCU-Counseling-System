from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_alter_clientprofile_department"),
    ]

    operations = [
        migrations.AddField(
            model_name="counselorprofile",
            name="cohort",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="수련 기수(예: 100). 관리자 승인 시 필수 입력.",
                null=True,
                verbose_name="기수",
            ),
        ),
    ]
