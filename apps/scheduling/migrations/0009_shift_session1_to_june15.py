"""1회기 확정 10건 +6일 조정 — 6/15 시작 (1회성)."""

from datetime import date

from django.db import migrations


def shift_session1_to_june15(apps, schema_editor):
    from apps.scheduling.auto_schedule_session1 import shift_session1_confirmed_schedule

    results = shift_session1_confirmed_schedule(
        dry_run=False,
        skip_availability=True,
        only_if_before=date(2026, 6, 15),
    )
    errors = [r for r in results if r.status == "error"]
    if errors:
        detail = "; ".join(f"{r.client_name}: {r.detail}" for r in errors)
        raise RuntimeError(f"session1 shift failed: {detail}")


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0008_counseloravailability_recurring_fields"),
    ]

    operations = [
        migrations.RunPython(shift_session1_to_june15, migrations.RunPython.noop),
    ]
