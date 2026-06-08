from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0010_alter_counselingapplication_reason"),
    ]

    operations = [
        migrations.AlterField(
            model_name="case",
            name="zoom_meeting_url",
            field=models.URLField(
                blank=True,
                help_text="상담 예약 시 Zoom API로 생성된 회의 URL(참가 링크)",
                max_length=2000,
                verbose_name="Zoom 회의 링크",
            ),
        ),
    ]
