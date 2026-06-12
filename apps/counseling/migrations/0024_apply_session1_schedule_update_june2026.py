"""1회기 매칭 변경 + 이영실 상담사 비활성화 (2026-06 운영)."""

from pathlib import Path

from django.conf import settings
from django.db import migrations

DEACTIVATED_COUNSELOR = "이영실"

CHANGED_CLIENTS = frozenset(
    {
        "조선혜",
        "정한결",
        "김선경",
        "홍연서",
    }
)


def apply_session1_schedule_update(apps, schema_editor):
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
        deactivate_counselor_by_name,
        import_session1_matches,
        load_session1_matches,
    )

    deactivated = deactivate_counselor_by_name(DEACTIVATED_COUNSELOR, dry_run=False)
    counselor = User.objects.filter(name=DEACTIVATED_COUNSELOR, role="COUNSELOR").first()
    if counselor and counselor.status != "INACTIVE":
        raise RuntimeError(f"{DEACTIVATED_COUNSELOR} 상담사 비활성화 실패")

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
        ("counseling", "0023_apply_session1_roster_update_june2026"),
    ]

    operations = [
        migrations.RunPython(apply_session1_schedule_update, migrations.RunPython.noop),
    ]
