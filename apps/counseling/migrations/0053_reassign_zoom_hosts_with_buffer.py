"""Zoom 호스트 30분 버퍼 — 배포 차단 방지용 플레이스홀더 (실제 재배정은 Shell에서 실행)."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def reassign_zoom_hosts_with_buffer(apps, schema_editor):
    """
    마이그레이션에서 Zoom API를 호출하면 preDeploy 실패 시 새 코드가 배포되지 않습니다.
    버퍼 배정 로직은 앱 코드에 포함되며, 기존 예약 재배정은 배포 후 수동 실행:
      python manage.py recreate_zoom_meetings --apply
      python manage.py ops_production_fixup --apply
    """
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    logger.info(
        "0053: Zoom 호스트 버퍼는 앱 코드에 반영됨. "
        "기존 예약 Zoom 재배정은 recreate_zoom_meetings --apply 로 실행하세요."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0052_pin_jeong_hangyeol_session2_zoom_host_02"),
    ]

    operations = [
        migrations.RunPython(
            reassign_zoom_hosts_with_buffer,
            migrations.RunPython.noop,
        ),
    ]
