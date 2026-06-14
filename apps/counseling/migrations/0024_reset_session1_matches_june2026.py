"""1회기 매칭 36건 전체 리셋 — 최신 일정·대면/비대면 반영 (2026-06 운영)."""

from pathlib import Path

from django.conf import settings
from django.db import migrations

DEACTIVATED_COUNSELORS = frozenset({"이영실"})
EXPECTED_MATCH_COUNT = 36


def reset_session1_matches(apps, schema_editor):
    from django.db import connection

    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.session1_bulk_import import (
        deactivate_counselor_by_name,
        import_session1_matches,
        load_session1_matches,
    )

    for counselor_name in DEACTIVATED_COUNSELORS:
        deactivate_counselor_by_name(counselor_name, dry_run=False)

    path = Path(settings.BASE_DIR) / "data" / "import" / "session1_matches_bulk_202606.json"
    rows = load_session1_matches(path)
    if len(rows) != EXPECTED_MATCH_COUNT:
        raise RuntimeError(
            f"매칭 JSON 건수 불일치: {len(rows)}건 (기대 {EXPECTED_MATCH_COUNT}건)"
        )

    summary = import_session1_matches(
        rows,
        dry_run=False,
        with_zoom=True,
        create_missing_application=True,
    )
    if summary.errors:
        messages = [r.message for r in summary.results if r.action == "error"]
        raise RuntimeError(
            f"1회기 매칭 전체 주입 실패 ({summary.errors}건): {'; '.join(messages)}"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0023_apply_session1_roster_update_june2026"),
    ]

    operations = [
        migrations.RunPython(reset_session1_matches, migrations.RunPython.noop),
    ]
