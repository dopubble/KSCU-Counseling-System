from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="appointment",
            name="unique_counselor_scheduled_at",
        ),
        migrations.AlterField(
            model_name="appointment",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "대기"),
                    ("SCHEDULED", "예약"),
                    ("CONFIRMED", "확정"),
                    ("COMPLETED", "완료"),
                    ("CANCELLED", "취소"),
                    ("NO_SHOW", "노쇼"),
                ],
                default="PENDING",
                max_length=20,
                verbose_name="상태",
            ),
        ),
        migrations.AddConstraint(
            model_name="appointment",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ["SCHEDULED", "CONFIRMED"])),
                fields=("counselor", "scheduled_at"),
                name="unique_counselor_confirmed_slot",
            ),
        ),
    ]
