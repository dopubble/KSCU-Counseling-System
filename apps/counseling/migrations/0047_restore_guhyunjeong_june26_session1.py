"""구현정 1회기 6/26 10:00 복구 (7/1로 밀려 있을 때)."""

import logging
from pathlib import Path

from django.conf import settings
from django.db import connection, migrations

logger = logging.getLogger(__name__)


def restore_guhyunjeong_june26_session1(apps, schema_editor):
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
        client_names=frozenset({"구현정"}),
    )
    for result in results:
        if result.status == "error":
            logger.error("구현정 1회기 복구 실패: %s", result.detail)
        else:
            logger.info("구현정 1회기 복구 %s: %s", result.status, result.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0046_pin_june26_11am_zoom_hosts"),
    ]

    operations = [
        migrations.RunPython(restore_guhyunjeong_june26_session1, migrations.RunPython.noop),
    ]
