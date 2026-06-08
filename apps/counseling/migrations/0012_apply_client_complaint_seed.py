"""스프레드시트 기준 주요 호소 문제 일괄 반영 (1회성)."""

from django.db import migrations


def apply_client_complaints(apps, schema_editor):
    from apps.counseling.client_complaint_update import update_client_complaints

    summary = update_client_complaints(dry_run=False, create_missing=True)
    if summary.errors:
        raise RuntimeError(
            f"client complaint seed failed: errors={summary.errors}, "
            f"missing_user={summary.missing_user}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("counseling", "0011_alter_case_zoom_meeting_url"),
    ]

    operations = [
        migrations.RunPython(apply_client_complaints, migrations.RunPython.noop),
    ]
