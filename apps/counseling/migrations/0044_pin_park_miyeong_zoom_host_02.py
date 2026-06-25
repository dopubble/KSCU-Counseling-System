"""박미영(이수정) 6/25 22:00 Zoom 호스트 → host_02 고정."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def pin_park_miyeong_zoom_host_02(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.ops_fixup import ensure_park_miyeong_zoom_host_02

    result = ensure_park_miyeong_zoom_host_02(dry_run=False)
    if result.status == "error":
        logger.error("박미영 Zoom host_02 배정 실패: %s", result.detail)
    else:
        logger.info("박미영 Zoom host_02 배정: %s — %s", result.status, result.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0043_sync_baeminjeong_session1_time_july2026"),
    ]

    operations = [
        migrations.RunPython(pin_park_miyeong_zoom_host_02, migrations.RunPython.noop),
    ]
