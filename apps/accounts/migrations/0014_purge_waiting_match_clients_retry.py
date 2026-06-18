"""매칭 대기 내담자 삭제 재시도 — 일부 미존재 시에도 찾은 계정은 삭제."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)


def purge_waiting_match_clients_retry(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.accounts.client_purge import (
        WAITING_MATCH_PURGE_JUNE2026,
        find_client_users_for_purge,
        purge_client_users,
    )

    matches, missing = find_client_users_for_purge(WAITING_MATCH_PURGE_JUNE2026)
    if missing:
        labels = ", ".join(t.label() for t in missing)
        logger.warning("매칭 대기 내담자 일부 미존재(건너뜀): %s", labels)
    if not matches:
        logger.info("매칭 대기 내담자 삭제 대상 없음")
        return

    result = purge_client_users(matches, dry_run=False)
    logger.info(
        "매칭 대기 내담자 삭제 완료: users=%s applications~=%s cases~=%s",
        result.deleted_users,
        result.deleted_applications,
        result.deleted_cases,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_purge_waiting_match_clients_june2026"),
    ]

    operations = [
        migrations.RunPython(purge_waiting_match_clients_retry, migrations.RunPython.noop),
    ]
