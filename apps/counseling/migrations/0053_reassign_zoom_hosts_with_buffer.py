"""Zoom 호스트 30분 완충 버퍼 적용 후 기존 확정 비대면 예약 호스트만 재배정."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def reassign_zoom_hosts_with_buffer(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.scheduling.services import fix_mismatched_zoom_host_assignments

    try:
        fixed, skipped, messages = fix_mismatched_zoom_host_assignments(
            dry_run=False,
            notify_link_change=False,
        )
    except Exception as exc:
        logger.error("Zoom 호스트 재배정 실패: %s", exc)
        raise

    errors = [m for m in messages if not m.startswith("[would fix]")]
    if errors:
        logger.error("Zoom 호스트 재배정 오류: %s", "; ".join(errors[:5]))
        raise RuntimeError(errors[0])

    logger.info(
        "Zoom 호스트 재배정(버퍼 적용): 수정 %s건, 건너뜀 %s건",
        fixed,
        skipped,
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
