"""6/26 캘린더 누락 복구 — 구현정·성순희 1회기 일시, 이세영 확정 점검."""

import logging
from pathlib import Path

from django.conf import settings
from django.db import connection, migrations

logger = logging.getLogger(__name__)

RESTORE_CLIENTS = frozenset({"구현정", "성순희"})


def restore_june26_session1_calendar(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.session1_bulk_import import (
        load_session1_matches,
        repair_session1_confirmations_from_roster,
        sync_session1_times_from_roster,
    )

    path = Path(settings.BASE_DIR) / "data" / "import" / "session1_matches_bulk_202606.json"
    rows = load_session1_matches(path)

    for client_name in sorted(RESTORE_CLIENTS):
        results = sync_session1_times_from_roster(
            rows,
            dry_run=False,
            skip_availability=True,
            client_names=frozenset({client_name}),
        )
        for result in results:
            if result.status == "error":
                logger.error(
                    "%s 1회기 일시 복구 실패: %s",
                    client_name,
                    result.detail,
                )
            else:
                logger.info(
                    "%s 1회기 일시 복구 %s: %s",
                    client_name,
                    result.status,
                    result.detail,
                )

    repair_results = repair_session1_confirmations_from_roster(
        rows,
        dry_run=False,
        skip_availability=True,
        client_names=frozenset({"이세영"}),
    )
    for result in repair_results:
        if result.status == "error":
            logger.error("이세영 1회기 복구 실패: %s", result.detail)
        else:
            logger.info("이세영 1회기 복구 %s: %s", result.status, result.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0044_pin_park_miyeong_zoom_host_02"),
    ]

    operations = [
        migrations.RunPython(restore_june26_session1_calendar, migrations.RunPython.noop),
    ]
