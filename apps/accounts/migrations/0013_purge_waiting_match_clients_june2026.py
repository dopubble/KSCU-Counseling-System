"""2026-06 매칭 대기 목록 테스트·중복 내담자 계정 완전 삭제."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def purge_waiting_match_clients(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.accounts.client_purge import purge_waiting_match_clients_june2026

    try:
        result = purge_waiting_match_clients_june2026(dry_run=False)
    except LookupError as exc:
        logger.warning("매칭 대기 내담자 삭제 스킵(일부 미존재): %s", exc)
        return

    logger.info(
        "매칭 대기 내담자 삭제 완료: users=%s applications~=%s cases~=%s",
        result.deleted_users,
        result.deleted_applications,
        result.deleted_cases,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_remove_orphan_counselor_profiles"),
    ]

    operations = [
        migrations.RunPython(purge_waiting_match_clients, migrations.RunPython.noop),
    ]
