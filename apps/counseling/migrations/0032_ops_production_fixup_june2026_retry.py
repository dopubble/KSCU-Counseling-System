"""운영 수정 재적용: 김장서율 삭제·김아름 1회기 확정."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def apply_ops_fixup(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.ops_fixup import apply_ops_production_fixup_june2026

    lines = apply_ops_production_fixup_june2026(dry_run=False)
    for line in lines:
        if line.status == "error":
            logger.error("ops_fixup %s: %s", line.task, line.detail)
        else:
            logger.info("ops_fixup %s: %s — %s", line.task, line.status, line.detail)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0031_force_kim_areum_session1_june2026"),
        ("accounts", "0015_purge_kim_jangseoyul"),
    ]

    operations = [
        migrations.RunPython(apply_ops_fixup, migrations.RunPython.noop),
    ]
