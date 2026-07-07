from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sessions_app", "0007_zoommeeting_zoom_host_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="zoommeeting",
            name="counselor_host_key",
            field=models.CharField(
                blank=True,
                default="",
                help_text="외부 Zoom 계정 회의 등 — 회기별 Claim Host 6자리. 비우면 ZOOM_HOST_KEY 환경변수.",
                max_length=20,
                verbose_name="상담사 호스트 키(Claim Host)",
            ),
        ),
    ]
