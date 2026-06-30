"""정한결 CASE-2026-0007 Zoom 회의 host_02 API 재생성 (삭제된 85365293781 대체)."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def recreate_jeonghangyeol_zoom_on_host_02(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.ops_fixup import ensure_jeonghangyeol_zoom_host_02

    result = ensure_jeonghangyeol_zoom_host_02(dry_run=False)
    if result.status == "error":
        logger.error("정한결 Zoom 재생성 실패: %s", result.detail)
    else:
        logger.info("정한결 Zoom 재생성: %s — %s", result.status, result.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0050_pin_jeonghangyeol_zoom_join_url"),
    ]

    operations = [
        migrations.RunPython(
            recreate_jeonghangyeol_zoom_on_host_02,
            migrations.RunPython.noop,
        ),
    ]
