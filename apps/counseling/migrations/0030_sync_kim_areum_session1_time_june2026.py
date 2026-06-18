"""백경미/김아름 1회기 일시 2026-06-25 16:00(대면) 확정."""

from pathlib import Path

from django.conf import settings
from django.db import connection, migrations


def sync_kim_areum_session1_time(apps, schema_editor):
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
        client_names=frozenset({"김아름"}),
    )
    errors = [r for r in results if r.status == "error"]
    if errors:
        import logging

        messages = "; ".join(f"{r.client_name}: {r.detail}" for r in errors)
        logging.getLogger(__name__).warning(
            "김아름 1회기 일시 확정 실패: %s",
            messages,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0029_sync_lee_gyeongsuk_session1_time_july2026"),
    ]

    operations = [
        migrations.RunPython(sync_kim_areum_session1_time, migrations.RunPython.noop),
    ]
