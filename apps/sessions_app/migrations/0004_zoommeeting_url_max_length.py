from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sessions_app", "0003_merge_20260604_1722"),
    ]

    operations = [
        migrations.AlterField(
            model_name="zoommeeting",
            name="join_url",
            field=models.URLField(max_length=2000, verbose_name="참가 URL"),
        ),
        migrations.AlterField(
            model_name="zoommeeting",
            name="start_url",
            field=models.URLField(blank=True, max_length=2000, verbose_name="호스트 URL"),
        ),
        migrations.AlterField(
            model_name="zoommeeting",
            name="recording_url",
            field=models.URLField(blank=True, max_length=2000, verbose_name="녹화 URL"),
        ),
    ]
