"""비대면 동시간대 상한(기본 2건) — 관리자 설정."""

from django.db import migrations, models


def create_default_zoom_settings(apps, schema_editor):
    RemoteZoomSchedulingSettings = apps.get_model(
        "scheduling", "RemoteZoomSchedulingSettings"
    )
    RemoteZoomSchedulingSettings.objects.get_or_create(
        pk=1,
        defaults={"simultaneous_session_capacity": 2},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0011_appointment_default_duration_50"),
    ]

    operations = [
        migrations.CreateModel(
            name="RemoteZoomSchedulingSettings",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "simultaneous_session_capacity",
                    models.PositiveSmallIntegerField(
                        default=2,
                        help_text="같은 시작 시각(예: 11:00)에 확정 가능한 비대면 상담 최대 건수. ZOOM_LICENSED_USERS에 host_03 등을 추가하면 10시·11시 엇갈림 배정에만 쓰이고, 이 값을 늘리지 않는 한 11시 3건은 불가합니다.",
                        verbose_name="동시간대 비대면 최대 건수",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Zoom 운영 설정",
                "verbose_name_plural": "Zoom 운영 설정",
            },
        ),
        migrations.RunPython(create_default_zoom_settings, migrations.RunPython.noop),
    ]
