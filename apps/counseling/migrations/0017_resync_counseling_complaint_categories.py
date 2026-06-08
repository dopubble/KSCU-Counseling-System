"""상담 호소 문제(*) 시드 재동기화 — 이메일·이름 매칭 보강 (1회성)."""

from django.db import migrations


def resync_counseling_complaint_categories(apps, schema_editor):
    from apps.counseling.client_complaint_update import update_client_complaints

    summary = update_client_complaints(dry_run=False, create_missing=False)
    if summary.errors:
        raise RuntimeError(
            f"complaint resync failed: errors={summary.errors}, "
            f"updated={summary.updated}, missing_user={summary.missing_user}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("counseling", "0016_apply_counseling_complaint_categories"),
    ]

    operations = [
        migrations.RunPython(
            resync_counseling_complaint_categories,
            migrations.RunPython.noop,
        ),
    ]
