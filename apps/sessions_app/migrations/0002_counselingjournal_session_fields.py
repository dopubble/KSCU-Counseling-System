from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sessions_app", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="counselingjournal",
            name="session_category",
            field=models.CharField(blank=True, max_length=50, verbose_name="상담 구분"),
        ),
        migrations.AddField(
            model_name="counselingjournal",
            name="session_datetime",
            field=models.DateTimeField(blank=True, null=True, verbose_name="상담 일시"),
        ),
    ]
