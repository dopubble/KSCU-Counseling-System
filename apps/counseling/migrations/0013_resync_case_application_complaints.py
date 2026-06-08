"""사례에 연결된 상담 신청 reason 재동기화 (1회성)."""

from django.db import migrations


def resync_case_application_complaints(apps, schema_editor):
    from apps.counseling.client_complaint_update import update_client_complaints

    summary = update_client_complaints(dry_run=False, create_missing=False)
    if summary.errors:
        raise RuntimeError(f"complaint resync failed: errors={summary.errors}")


class Migration(migrations.Migration):
    dependencies = [
        ("counseling", "0012_apply_client_complaint_seed"),
    ]

    operations = [
        migrations.RunPython(
            resync_case_application_complaints,
            migrations.RunPython.noop,
        ),
    ]
