"""정한결 CASE-2026-0007 수동 Zoom join URL 고정 (85365293781)."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def pin_jeonghangyeol_zoom_join_url(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.ops_fixup import ensure_jeonghangyeol_zoom_host_02

    result = ensure_jeonghangyeol_zoom_host_02(dry_run=False)
    if result.status == "error":
        logger.error("정한결 Zoom join URL 고정 실패: %s", result.detail)
    else:
        logger.info("정한결 Zoom join URL 고정: %s — %s", result.status, result.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0049_pin_jeonghangyeol_zoom_host_02"),
    ]

    operations = [
        migrations.RunPython(pin_jeonghangyeol_zoom_join_url, migrations.RunPython.noop),
    ]
