"""예약 기본 상담 시간 50분 → 60분."""

from django.db import migrations, models

import apps.scheduling.constants


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0009_shift_session1_to_june15"),
    ]

    operations = [
        migrations.AlterField(
            model_name="appointment",
            name="duration_minutes",
            field=models.PositiveIntegerField(
                default=apps.scheduling.constants.DEFAULT_APPOINTMENT_DURATION_MINUTES,
                verbose_name="상담 시간(분)",
            ),
        ),
    ]
