"""김아름 1회기 2026-06-25 16:00 강제 확정 (로스터 상담사명 무관)."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def force_kim_areum_session1(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.session1_bulk_import import force_client_session1_schedule

    scheduled_at = datetime(2026, 6, 25, 16, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    result = force_client_session1_schedule(
        client_name="김아름",
        scheduled_at=scheduled_at,
        dry_run=False,
        skip_availability=True,
    )
    if result.status == "error":
        logger.warning("김아름 1회기 강제 확정 실패: %s", result.detail)
        return
    logger.info("김아름 1회기 강제 확정: %s", result.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0030_sync_kim_areum_session1_time_june2026"),
    ]

    operations = [
        migrations.RunPython(force_kim_areum_session1, migrations.RunPython.noop),
    ]
