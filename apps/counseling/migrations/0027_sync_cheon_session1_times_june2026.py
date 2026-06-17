"""천옥희 담당 1회기 일시 — 골드 JSON과 DB 동기화 (조영은 2026-06-23 14:00 등)."""

from pathlib import Path

from django.conf import settings
from django.db import migrations


def sync_cheon_session1_times(apps, schema_editor):
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
        counselor_name="천옥희",
    )
    errors = [r for r in results if r.status == "error"]
    if errors:
        messages = "; ".join(f"{r.client_name}: {r.detail}" for r in errors)
        raise RuntimeError(f"1회기 일시 동기화 실패 ({len(errors)}건): {messages}")


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0026_kim_hyejeong_remote_counseling_method"),
    ]

    operations = [
        migrations.RunPython(sync_cheon_session1_times, migrations.RunPython.noop),
    ]
