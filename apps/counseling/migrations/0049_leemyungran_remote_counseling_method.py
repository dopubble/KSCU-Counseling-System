"""이명란(starking0700@naver.com) 내담자 상담 방식: 대면 → 비대면 + Zoom 연결."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)

CLIENT_EMAIL = "starking0700@naver.com"
CLIENT_NAME = "이명란"


def set_leemyungran_remote_with_zoom(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.ops_fixup import switch_client_to_remote_with_zoom

    result = switch_client_to_remote_with_zoom(
        client_name=CLIENT_NAME,
        client_email=CLIENT_EMAIL,
        dry_run=False,
    )
    if result.status == "error":
        logger.error("이명란 비대면 전환 실패: %s", result.detail)
        return
    logger.info("이명란 비대면 전환: %s — %s", result.status, result.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0048_sync_guhyunjeong_session1_time_july2026"),
    ]

    operations = [
        migrations.RunPython(set_leemyungran_remote_with_zoom, migrations.RunPython.noop),
    ]
