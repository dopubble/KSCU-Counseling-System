import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0011_alter_case_zoom_meeting_url"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("sessions_app", "0004_zoommeeting_url_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="InitialCounselingRecord",
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
                (
                    "session_start_datetime",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="상담 시작 일시"
                    ),
                ),
                (
                    "presented_problems_summary",
                    models.TextField(
                        blank=True, verbose_name="제시된 문제·주제·패턴·현재 상태 요약"
                    ),
                ),
                (
                    "functioning_impact",
                    models.TextField(
                        blank=True, verbose_name="현재와 과거의 기능 및 문제의 영향"
                    ),
                ),
                (
                    "relational_history",
                    models.TextField(blank=True, verbose_name="관계적 역사"),
                ),
                (
                    "clinical_history",
                    models.TextField(blank=True, verbose_name="임상적 역사"),
                ),
                (
                    "theological_evaluation",
                    models.TextField(blank=True, verbose_name="신학적 평가"),
                ),
                (
                    "clinical_strategy",
                    models.TextField(blank=True, verbose_name="임상적 전략"),
                ),
                ("other_notes", models.TextField(blank=True, verbose_name="기타")),
                ("is_draft", models.BooleanField(default=True, verbose_name="임시저장")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "case",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="initial_counseling_record",
                        to="counseling.case",
                        verbose_name="사례",
                    ),
                ),
                (
                    "counselor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="initial_counseling_records",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="상담사",
                    ),
                ),
            ],
            options={
                "verbose_name": "초기상담 기록지",
                "verbose_name_plural": "초기상담 기록지",
            },
        ),
    ]
