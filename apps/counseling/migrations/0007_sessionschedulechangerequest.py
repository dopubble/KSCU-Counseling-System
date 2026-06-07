import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0002_appointment_pending_status"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("counseling", "0006_remove_counselingapplication_urgency"),
    ]

    operations = [
        migrations.CreateModel(
            name="SessionScheduleChangeRequest",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("session_number", models.PositiveIntegerField(verbose_name="회차")),
                (
                    "preferred_datetime",
                    models.DateTimeField(blank=True, null=True, verbose_name="희망 일시"),
                ),
                ("message", models.TextField(verbose_name="요청 내용")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "appointment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="schedule_change_requests",
                        to="scheduling.appointment",
                        verbose_name="예약",
                    ),
                ),
                (
                    "case",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="schedule_change_requests",
                        to="counseling.case",
                        verbose_name="사례",
                    ),
                ),
                (
                    "client",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="schedule_change_requests",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="내담자",
                    ),
                ),
            ],
            options={
                "verbose_name": "일정 변경 요청",
                "verbose_name_plural": "일정 변경 요청",
                "ordering": ["-created_at"],
            },
        ),
    ]
