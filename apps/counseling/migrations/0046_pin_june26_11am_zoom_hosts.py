"""6/26 11:00 성순희(host_02)·김수미(host_01) Zoom 호스트 pin."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def pin_june26_11am_zoom_hosts(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.ops_fixup import (
        ensure_kim_sumi_zoom_host_01,
        ensure_soonsunhee_zoom_host_02,
    )

    for label, runner in (
        ("김수미 host_01", ensure_kim_sumi_zoom_host_01),
        ("성순희 host_02", ensure_soonsunhee_zoom_host_02),
    ):
        result = runner(dry_run=False)
        if result.status == "error":
            logger.error("%s 배정 실패: %s", label, result.detail)
        else:
            logger.info("%s 배정: %s — %s", label, result.status, result.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0045_restore_june26_session1_calendar"),
    ]

    operations = [
        migrations.RunPython(pin_june26_11am_zoom_hosts, migrations.RunPython.noop),
    ]
