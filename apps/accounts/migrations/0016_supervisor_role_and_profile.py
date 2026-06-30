# Generated manually for SUPERVISOR role and SupervisorProfile.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0015_purge_kim_jangseoyul"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("ADMIN", "관리자"),
                    ("SUPERVISOR", "수퍼바이저"),
                    ("COUNSELOR", "상담사"),
                    ("CLIENT", "내담자"),
                ],
                default="CLIENT",
                max_length=20,
                verbose_name="역할",
            ),
        ),
        migrations.CreateModel(
            name="SupervisorProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "assigned_cohorts",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="담당 수련 기수 목록. 예: [1, 2]",
                        verbose_name="담당 기수",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="supervisor_profile",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="사용자",
                    ),
                ),
            ],
            options={
                "verbose_name": "수퍼바이저 프로필",
                "verbose_name_plural": "수퍼바이저 프로필",
            },
        ),
    ]
