from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0007_appointment_request_message"),
    ]

    operations = [
        migrations.AddField(
            model_name="counseloravailability",
            name="is_recurring",
            field=models.BooleanField(default=True, verbose_name="매주 반복"),
        ),
        migrations.AddField(
            model_name="counseloravailability",
            name="specific_date",
            field=models.DateField(blank=True, null=True, verbose_name="특정 날짜"),
        ),
        migrations.AddField(
            model_name="counseloravailability",
            name="is_available",
            field=models.BooleanField(default=True, verbose_name="상담 가능"),
        ),
        migrations.AlterField(
            model_name="counseloravailability",
            name="day_of_week",
            field=models.IntegerField(
                blank=True,
                help_text="0=월요일, 6=일요일",
                null=True,
                verbose_name="요일",
            ),
        ),
        migrations.AlterModelOptions(
            name="counseloravailability",
            options={
                "ordering": ["-is_recurring", "specific_date", "day_of_week", "start_time"],
                "verbose_name": "상담 가능 시간",
                "verbose_name_plural": "상담 가능 시간",
            },
        ),
    ]
