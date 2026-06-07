from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0005_appointment_cancel_requested_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="appointment",
            name="session_number",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="내담자 회기별 예약 신청 시 연결되는 상담 회기 번호",
                null=True,
                verbose_name="회차",
            ),
        ),
    ]
