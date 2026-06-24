"""백경미 담당 황윤진·김아름 1회기 일시 수정 (14:00·11:00)."""

import logging
from pathlib import Path

from django.conf import settings
from django.db import connection, migrations

logger = logging.getLogger(__name__)


def sync_baekgyeongmi_session1_times(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.session1_bulk_import import (
        load_session1_matches,
        repair_session1_confirmations_from_roster,
    )

    path = Path(settings.BASE_DIR) / "data" / "import" / "session1_matches_bulk_202606.json"
    rows = load_session1_matches(path)
    results = repair_session1_confirmations_from_roster(
        rows,
        dry_run=False,
        skip_availability=True,
        counselor_name="백경미",
        client_names=frozenset({"황윤진", "김아름"}),
    )
    for result in results:
        if result.status == "error":
            logger.error(
                "백경미 1회기 일시 수정 실패 %s: %s",
                result.client_name,
                result.detail,
            )
        else:
            logger.info(
                "백경미 1회기 일시 수정 %s: %s — %s",
                result.client_name,
                result.status,
                result.detail,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0038_fix_zoom_host_mismatches"),
    ]

    operations = [
        migrations.RunPython(sync_baekgyeongmi_session1_times, migrations.RunPython.noop),
    ]
