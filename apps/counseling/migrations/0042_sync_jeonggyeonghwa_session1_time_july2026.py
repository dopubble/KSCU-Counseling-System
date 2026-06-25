"""신영화/정경화 1회기 일시 2026-07-03 11:00 동기화."""

import logging
from pathlib import Path

from django.conf import settings
from django.db import connection, migrations

logger = logging.getLogger(__name__)


def sync_jeonggyeonghwa_session1_time(apps, schema_editor):
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
        counselor_name="신영화",
        client_names=frozenset({"정경화"}),
    )
    for result in results:
        if result.status == "error":
            logger.error(
                "정경화 1회기 일시 수정 실패: %s",
                result.detail,
            )
        else:
            logger.info(
                "정경화 1회기 일시 수정 %s: %s",
                result.status,
                result.detail,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0041_soonsunhee_remote_counseling_method"),
    ]

    operations = [
        migrations.RunPython(sync_jeonggyeonghwa_session1_time, migrations.RunPython.noop),
    ]
