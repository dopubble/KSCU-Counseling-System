"""성순희(sooni1028@naver.com) 내담자 상담 방식: 대면 → 비대면 + Zoom 연결."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)

CLIENT_EMAIL = "sooni1028@naver.com"
CLIENT_NAME = "성순희"


def set_soonsunhee_remote(apps, schema_editor):
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
        logger.error("성순희 비대면 전환 실패: %s", result.detail)
        return
    logger.info("성순희 비대면 전환: %s — %s", result.status, result.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0040_force_kim_areum_session1_11am"),
    ]

    operations = [
        migrations.RunPython(set_soonsunhee_remote, migrations.RunPython.noop),
    ]
