"""상담 호소 문제(유형)와 작성 원문 분리 반영 (1회성)."""

from django.db import migrations


def split_complaint_fields(apps, schema_editor):
    from apps.counseling.client_complaint_update import update_client_complaints

    summary = update_client_complaints(dry_run=False, create_missing=False)
    if summary.errors:
        raise RuntimeError(
            f"split complaint fields failed: errors={summary.errors}, "
            f"updated={summary.updated}, missing_user={summary.missing_user}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("counseling", "0017_resync_counseling_complaint_categories"),
    ]

    operations = [
        migrations.RunPython(split_complaint_fields, migrations.RunPython.noop),
    ]
