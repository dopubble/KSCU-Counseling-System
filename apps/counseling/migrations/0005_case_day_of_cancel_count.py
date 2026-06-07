from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0004_case_session_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="day_of_cancel_count",
            field=models.PositiveIntegerField(
                default=0,
                help_text="예약 당일 취소 요청이 누적된 횟수(3회 이상 시 조기 종결)",
                verbose_name="당일 취소 횟수",
            ),
        ),
    ]
