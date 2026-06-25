"""김은영/배민정 1회기 비대면 2026-07-25 19:30 동기화."""

import logging
from pathlib import Path

from django.conf import settings
from django.db import connection, migrations

logger = logging.getLogger(__name__)

CLIENT_EMAIL = "yhamom@naver.com"
CLIENT_NAME = "배민정"
COUNSELOR_NAME = "김은영"


def sync_baeminjeong_session1_time(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.ops_fixup import switch_client_to_remote_with_zoom
    from apps.counseling.session1_bulk_import import (
        load_session1_matches,
        sync_session1_times_from_roster,
    )

    remote_result = switch_client_to_remote_with_zoom(
        client_name=CLIENT_NAME,
        client_email=CLIENT_EMAIL,
        dry_run=False,
    )
    if remote_result.status == "error":
        logger.error("배민정 비대면 전환: %s", remote_result.detail)
    else:
        logger.info("배민정 비대면 전환: %s — %s", remote_result.status, remote_result.detail)

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
            logger.error("배민정 1회기 일시 수정 실패: %s", result.detail)
        else:
            logger.info(
                "배민정 1회기 일시 수정 %s: %s",
                result.status,
                result.detail,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0042_sync_jeonggyeonghwa_session1_time_july2026"),
    ]

    operations = [
        migrations.RunPython(sync_baeminjeong_session1_time, migrations.RunPython.noop),
    ]
