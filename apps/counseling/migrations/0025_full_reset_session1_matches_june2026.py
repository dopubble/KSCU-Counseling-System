"""1회기 매칭 전량 리셋 + 36건 재주입 + 무결성 검증 (잔존 배정 제거)."""

from pathlib import Path

from django.conf import settings
from django.db import migrations

DEACTIVATED_COUNSELORS = frozenset({"이영실"})
EXPECTED_MATCH_COUNT = 36


def full_reset_and_verify_session1(apps, schema_editor):
    from django.db import connection

    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    from apps.counseling.session1_bulk_import import (
        assert_session1_roster,
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
        full_reset=True,
        verify=True,
    )
    if summary.errors:
        messages = [r.message for r in summary.results if r.action == "error"]
        raise RuntimeError(
            f"1회기 매칭 전체 주입 실패 ({summary.errors}건): {'; '.join(messages)}"
        )

    assert_session1_roster(rows)


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0024_reset_session1_matches_june2026"),
    ]

    operations = [
        migrations.RunPython(full_reset_and_verify_session1, migrations.RunPython.noop),
    ]
