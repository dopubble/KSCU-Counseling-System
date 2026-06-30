"""박미영(CASE-2026-0025) 2회기 6/30 20:00 Zoom 호스트 → host_02 고정."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def pin_park_miyeong_session2_zoom_host_02(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.ops_fixup import ensure_park_miyeong_session2_zoom_host_02

    result = ensure_park_miyeong_session2_zoom_host_02(dry_run=False)
    if result.status == "error":
        logger.error("박미영 2회기 Zoom host_02 배정 실패: %s", result.detail)
    else:
        logger.info("박미영 2회기 Zoom host_02 배정: %s — %s", result.status, result.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0049_leemyungran_remote_counseling_method"),
    ]

    operations = [
        migrations.RunPython(
            pin_park_miyeong_session2_zoom_host_02,
            migrations.RunPython.noop,
        ),
    ]
