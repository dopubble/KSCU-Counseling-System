from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sessions_app", "0010_backfill_journal_session_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="zoommeeting",
            name="zoom_account_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="회의 생성 시점의 ZOOM_ACCOUNT_ID 스냅샷. 비어 있으면 기존(legacy) Zoom 계정에서 만든 회의로 표시합니다.",
                max_length=128,
                verbose_name="생성 시 Zoom Account ID",
            ),
        ),
    ]
