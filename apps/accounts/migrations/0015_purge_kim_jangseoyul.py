"""김장서율(261110004) 내담자 계정 완전 삭제."""

import logging

from django.db import connection, migrations

logger = logging.getLogger(__name__)

_PURGE_TARGETS = (("김장서율", "261110004"),)


def purge_kim_jangseoyul(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.accounts.client_purge import (
        ClientPurgeTarget,
        find_client_users_for_purge,
        purge_client_users,
    )

    targets = tuple(ClientPurgeTarget(name, sid) for name, sid in _PURGE_TARGETS)
    matches, missing = find_client_users_for_purge(targets)
    if missing:
        labels = ", ".join(t.label() for t in missing)
        logger.warning("김장서율 삭제 스킵(미존재): %s", labels)
        return
    if not matches:
        return

    result = purge_client_users(matches, dry_run=False)
    logger.info(
        "김장서율 삭제 완료: users=%s applications~=%s cases~=%s",
        result.deleted_users,
        result.deleted_applications,
        result.deleted_cases,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_purge_waiting_match_clients_retry"),
    ]

    operations = [
        migrations.RunPython(purge_kim_jangseoyul, migrations.RunPython.noop),
    ]
