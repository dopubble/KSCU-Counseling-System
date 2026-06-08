"""스프레드시트 원문 주요 호소 문제로 재반영 (1회성)."""

from django.db import migrations


def apply_full_complaint_texts(apps, schema_editor):
    from apps.counseling.client_complaint_update import update_client_complaints

    summary = update_client_complaints(dry_run=False, create_missing=False)
    if summary.errors:
        raise RuntimeError(f"full complaint sync failed: errors={summary.errors}")


class Migration(migrations.Migration):
    dependencies = [
        ("counseling", "0013_resync_case_application_complaints"),
    ]

    operations = [
        migrations.RunPython(apply_full_complaint_texts, migrations.RunPython.noop),
    ]
