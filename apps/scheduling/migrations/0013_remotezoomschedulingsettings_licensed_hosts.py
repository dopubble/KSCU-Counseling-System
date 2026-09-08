from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("scheduling", "0012_remote_zoom_scheduling_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="remotezoomschedulingsettings",
            name="licensed_user_emails",
            field=models.TextField(
                blank=True,
                default="",
                help_text="한 줄에 이메일 하나. 위부터 host_01, host_02, … 순서입니다. 비우면 Railway ZOOM_LICENSED_USERS(없으면 코드 기본값)를 사용합니다.",
                verbose_name="Zoom 상담 호스트",
            ),
        ),
        migrations.AddField(
            model_name="remotezoomschedulingsettings",
            name="last_verified_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="최근 Zoom 연결 테스트",
            ),
        ),
        migrations.AddField(
            model_name="remotezoomschedulingsettings",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
                verbose_name="수정자",
            ),
        ),
    ]
