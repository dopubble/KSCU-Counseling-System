from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0007_sessionschedulechangerequest"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sessionschedulechangerequest",
            name="message",
            field=models.TextField(blank=True, verbose_name="요청 내용"),
        ),
    ]
