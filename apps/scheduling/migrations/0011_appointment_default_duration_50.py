"""예약 기본 상담 시간 60분 → 50분."""

from django.db import migrations, models

import apps.scheduling.constants


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0010_appointment_default_duration_60"),
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
