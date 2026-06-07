from django.db import migrations, models
from django.db.models import F


def backfill_confirmed_at(apps, schema_editor):
    Appointment = apps.get_model("scheduling", "Appointment")
    Appointment.objects.filter(
        status="CONFIRMED",
        confirmed_at__isnull=True,
    ).update(confirmed_at=F("updated_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0002_appointment_pending_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="appointment",
            name="confirmed_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="확정일"),
        ),
        migrations.RunPython(backfill_confirmed_at, migrations.RunPython.noop),
    ]
