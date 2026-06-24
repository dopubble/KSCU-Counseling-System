"""운영 DB: 겹치는 비대면 예약 Zoom 호스트 배정 불일치 수정."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def fix_zoom_host_mismatches(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.ops_fixup import fix_zoom_host_mismatches as run_fixup

    line = run_fixup(dry_run=False)
    if line.status == "error":
        logger.error("fix_zoom_host_mismatches: %s", line.detail)
    else:
        logger.info("fix_zoom_host_mismatches: %s — %s", line.status, line.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0037_leemyungran_in_person_counseling_method"),
        ("sessions_app", "0007_zoommeeting_zoom_host_email"),
    ]

    operations = [
        migrations.RunPython(fix_zoom_host_mismatches, migrations.RunPython.noop),
    ]
