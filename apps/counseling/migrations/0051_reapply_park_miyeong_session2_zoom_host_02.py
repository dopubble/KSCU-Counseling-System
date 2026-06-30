"""박미영 2회기 Zoom host_02 재적용 — preDeploy fixup 순서 보정 후."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def reapply_park_miyeong_session2_zoom_host_02(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.ops_fixup import ensure_park_miyeong_session2_zoom_host_02

    result = ensure_park_miyeong_session2_zoom_host_02(dry_run=False)
    if result.status == "error":
        logger.error("박미영 2회기 Zoom host_02 재배정 실패: %s", result.detail)
        raise RuntimeError(result.detail)
    logger.info("박미영 2회기 Zoom host_02 재배정: %s — %s", result.status, result.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0050_pin_park_miyeong_session2_zoom_host_02"),
    ]

    operations = [
        migrations.RunPython(
            reapply_park_miyeong_session2_zoom_host_02,
            migrations.RunPython.noop,
        ),
    ]
