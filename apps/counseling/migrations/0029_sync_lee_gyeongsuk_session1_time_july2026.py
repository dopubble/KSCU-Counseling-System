"""김소진/이경숙 1회기 일시 2026-07-10 14:00(비대면) 동기화."""

from pathlib import Path

from django.conf import settings
from django.db import migrations


def sync_lee_gyeongsuk_session1_time(apps, schema_editor):
    from django.db import connection

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
        counselor_name="김소진",
        client_names=frozenset({"이경숙"}),
    )
    errors = [r for r in results if r.status == "error"]
    if errors:
        import logging

        messages = "; ".join(f"{r.client_name}: {r.detail}" for r in errors)
        logging.getLogger(__name__).warning(
            "이경숙 1회기 일시 동기화 실패: %s",
            messages,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0028_sync_go_hyesuk_session1_time_june2026"),
    ]

    operations = [
        migrations.RunPython(sync_lee_gyeongsuk_session1_time, migrations.RunPython.noop),
    ]
