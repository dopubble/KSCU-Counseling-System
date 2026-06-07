from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0004_appointment_cancel_pending_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="appointment",
            name="cancel_reason",
            field=models.TextField(
                blank=True,
                help_text="내담자 취소 요청 또는 관리자 취소 처리 시 입력",
                verbose_name="취소 사유",
            ),
        ),
        migrations.AddField(
            model_name="appointment",
            name="cancel_requested_at",
            field=models.DateTimeField(
                blank=True,
                help_text="내담자가 취소 요청(CANCEL_PENDING)을 제출한 시각",
                null=True,
                verbose_name="취소 요청일",
            ),
        ),
    ]
