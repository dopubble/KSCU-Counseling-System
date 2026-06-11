"""1회기 매칭 6건 변경 — 상담사·일정·대면/비대면 반영 (2026-06 운영)."""

from pathlib import Path

from django.conf import settings
from django.db import migrations

CHANGED_CLIENTS = frozenset(
    {
        "구현정",
        "조현경",
        "장경화",
        "조선혜",
        "이지현",
        "임유정",
    }
)


def apply_session1_roster_update(apps, schema_editor):
    from django.db import connection

    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    User = apps.get_model("accounts", "User")
    present = set(
        User.objects.filter(name__in=CHANGED_CLIENTS).values_list("name", flat=True)
    )
    if not present:
        return
    missing = CHANGED_CLIENTS - present
    if missing:
        raise RuntimeError(
            f"1회기 매칭 변경 대상 내담자 없음: {', '.join(sorted(missing))}"
        )

    from apps.counseling.session1_bulk_import import (
        import_session1_matches,
        load_session1_matches,
    )

    path = Path(settings.BASE_DIR) / "data" / "import" / "session1_matches_bulk_202606.json"
    rows = load_session1_matches(path)
    subset = [row for row in rows if row.client_name in CHANGED_CLIENTS]
    if len(subset) != len(CHANGED_CLIENTS):
        found = {row.client_name for row in subset}
        missing = CHANGED_CLIENTS - found
        raise RuntimeError(f"매칭 JSON에 변경 대상 누락: {', '.join(sorted(missing))}")

    summary = import_session1_matches(
        subset,
        dry_run=False,
        with_zoom=True,
        create_missing_application=True,
    )
    if summary.errors:
        messages = [r.message for r in summary.results if r.action == "error"]
        raise RuntimeError(
            f"1회기 매칭 변경 실패 ({summary.errors}건): {'; '.join(messages)}"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0022_chatmessage_is_read"),
    ]

    operations = [
        migrations.RunPython(apply_session1_roster_update, migrations.RunPython.noop),
    ]
