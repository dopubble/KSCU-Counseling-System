"""스프레드시트 상담 호소 문제(*) 컬럼 재반영 (1회성)."""

from django.db import migrations


def apply_counseling_complaint_categories(apps, schema_editor):
    from apps.counseling.client_complaint_update import update_client_complaints

    summary = update_client_complaints(dry_run=False, create_missing=False)
    if summary.errors:
        raise RuntimeError(
            f"complaint category sync failed: errors={summary.errors}, "
            f"updated={summary.updated}, missing_user={summary.missing_user}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("counseling", "0015_force_full_complaint_texts"),
    ]

    operations = [
        migrations.RunPython(
            apply_counseling_complaint_categories,
            migrations.RunPython.noop,
        ),
    ]
