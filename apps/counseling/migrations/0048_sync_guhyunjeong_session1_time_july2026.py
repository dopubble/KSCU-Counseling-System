"""양은영/구현정 1회기 비대면 2026-07-01 10:00 동기화."""

import logging
from pathlib import Path

from django.conf import settings
from django.db import connection, migrations

logger = logging.getLogger(__name__)

CLIENT_NAME = "구현정"
COUNSELOR_NAME = "양은영"


def sync_guhyunjeong_session1_time(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.session1_bulk_import import (
        load_session1_matches,
        sync_session1_times_from_roster,
    )

    path = Path(settings.BASE_DIR) / "data" / "import" / "session1_matches_bulk_202606.json"
    rows = load_session1_matches(path)
    results = sync_session1_times_from_roster(
        rows,
        dry_run=False,
        skip_availability=True,
        counselor_name=COUNSELOR_NAME,
        client_names=frozenset({CLIENT_NAME}),
    )
    for result in results:
        if result.status == "error":
            logger.error("구현정 1회기 일시 수정 실패: %s", result.detail)
        else:
            logger.info(
                "구현정 1회기 일시 수정 %s: %s",
                result.status,
                result.detail,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0047_restore_guhyunjeong_june26_session1"),
    ]

    operations = [
        migrations.RunPython(sync_guhyunjeong_session1_time, migrations.RunPython.noop),
    ]
