"""김아름(arsui90@naver.com) 1회기 2026-06-25 11:00 강제 확정."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import connection, migrations

logger = logging.getLogger(__name__)

KIM_AREUM_EMAIL = "arsui90@naver.com"
KIM_AREUM_SESSION1_AT = datetime(2026, 6, 25, 11, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def force_kim_areum_session1_11am(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.session1_bulk_import import force_client_session1_schedule

    result = force_client_session1_schedule(
        client_name="김아름",
        client_email=KIM_AREUM_EMAIL,
        scheduled_at=KIM_AREUM_SESSION1_AT,
        dry_run=False,
        skip_availability=True,
    )
    if result.status == "error":
        logger.error("김아름 1회기 11:00 강제 확정 실패: %s", result.detail)
        return
    logger.info("김아름 1회기 11:00 강제 확정: %s", result.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0039_sync_baekgyeongmi_session1_times"),
    ]

    operations = [
        migrations.RunPython(force_kim_areum_session1_11am, migrations.RunPython.noop),
    ]
