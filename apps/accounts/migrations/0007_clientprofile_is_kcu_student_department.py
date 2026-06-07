from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_alter_clientprofile_student_id_alter_user_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientprofile",
            name="is_kcu_student",
            field=models.BooleanField(
                default=False,
                verbose_name="숭실사이버대학교 학생 여부",
            ),
        ),
        migrations.AddField(
            model_name="clientprofile",
            name="department",
            field=models.CharField(
                blank=True,
                max_length=100,
                verbose_name="소속 학과",
            ),
        ),
        migrations.AlterField(
            model_name="clientprofile",
            name="birth_date",
            field=models.DateField(
                blank=True,
                help_text="회원가입 시 확정되며 이후 변경할 수 없습니다.",
                null=True,
                verbose_name="생년월일",
            ),
        ),
        migrations.AlterField(
            model_name="clientprofile",
            name="student_id",
            field=models.CharField(
                blank=True,
                help_text="선택 사항. 회원가입·상담 신청 시 확정되며 이후 변경할 수 없습니다.",
                max_length=20,
                verbose_name="학번",
            ),
        ),
    ]
