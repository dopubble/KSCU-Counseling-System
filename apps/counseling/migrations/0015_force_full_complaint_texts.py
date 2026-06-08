"""주요 호소 문제 원문 강제 재동기화 (1회성)."""

from django.db import migrations


def force_full_complaint_texts(apps, schema_editor):
    from apps.counseling.client_complaint_update import update_client_complaints

    summary = update_client_complaints(dry_run=False, create_missing=False)
    if summary.errors:
        raise RuntimeError(
            f"force complaint sync failed: errors={summary.errors}, "
            f"updated={summary.updated}, missing_user={summary.missing_user}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("counseling", "0014_apply_full_complaint_texts"),
    ]

    operations = [
        migrations.RunPython(force_full_complaint_texts, migrations.RunPython.noop),
    ]
