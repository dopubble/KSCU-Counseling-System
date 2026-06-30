"""정한결(CASE-2026-0007) 2회기 7/7 15:00 Zoom 호스트 → host_02 고정."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def pin_jeong_hangyeol_session2_zoom_host_02(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.ops_fixup import ensure_jeong_hangyeol_session2_zoom_host_02

    result = ensure_jeong_hangyeol_session2_zoom_host_02(dry_run=False)
    if result.status == "error":
        logger.error("정한결 2회기 Zoom host_02 배정 실패: %s", result.detail)
        raise RuntimeError(result.detail)
    logger.info("정한결 2회기 Zoom host_02 배정: %s — %s", result.status, result.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0051_reapply_park_miyeong_session2_zoom_host_02"),
    ]

    operations = [
        migrations.RunPython(
            pin_jeong_hangyeol_session2_zoom_host_02,
            migrations.RunPython.noop,
        ),
    ]
