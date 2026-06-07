from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="zoom_meeting_url",
            field=models.URLField(
                blank=True,
                help_text="상담 예약 시 Zoom API로 생성된 회의 URL",
                max_length=500,
                verbose_name="Zoom 회의 링크",
            ),
        ),
    ]
