from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0018_split_complaint_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="counselingapplication",
            name="clinical_diagnosis",
            field=models.TextField(blank=True, default="", verbose_name="병원 진단명"),
        ),
        migrations.AddField(
            model_name="counselingapplication",
            name="current_medication",
            field=models.TextField(
                blank=True,
                default="",
                help_text="관련 약물 없으면 '없음'",
                verbose_name="복용 중인 약",
            ),
        ),
        migrations.AddField(
            model_name="counselingapplication",
            name="occupation",
            field=models.CharField(
                blank=True, default="", max_length=100, verbose_name="직업"
            ),
        ),
        migrations.AddField(
            model_name="counselingapplication",
            name="residence_region",
            field=models.CharField(
                blank=True,
                default="",
                help_text="국내: 시·도 단위 / 해외: 국가명 포함",
                max_length=200,
                verbose_name="거주지역",
            ),
        ),
    ]
