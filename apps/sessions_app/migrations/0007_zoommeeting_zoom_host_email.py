from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sessions_app", "0006_terminationcounselingrecord"),
    ]

    operations = [
        migrations.AddField(
            model_name="zoommeeting",
            name="zoom_host_email",
            field=models.EmailField(
                blank=True,
                default="",
                help_text="회의를 생성한 Zoom Licensed 사용자 이메일",
                max_length=254,
                verbose_name="Zoom 호스트(Licensed 사용자)",
            ),
        ),
    ]
