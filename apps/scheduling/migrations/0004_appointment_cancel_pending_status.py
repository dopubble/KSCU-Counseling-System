from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0003_appointment_confirmed_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="appointment",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "대기"),
                    ("SCHEDULED", "예약"),
                    ("CONFIRMED", "확정"),
                    ("CANCEL_PENDING", "취소 대기 중"),
                    ("COMPLETED", "완료"),
                    ("CANCELLED", "취소"),
                    ("NO_SHOW", "노쇼"),
                ],
                default="PENDING",
                max_length=20,
                verbose_name="상태",
            ),
        ),
    ]
