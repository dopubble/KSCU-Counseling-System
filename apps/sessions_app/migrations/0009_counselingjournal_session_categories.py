from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sessions_app", "0008_zoommeeting_counselor_host_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="counselingjournal",
            name="session_categories",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="신규 작성 시점의 상담 신청 상담 구분 스냅샷",
                verbose_name="상담 구분 목록",
            ),
        ),
    ]
