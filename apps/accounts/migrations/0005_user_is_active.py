from django.db import migrations, models


def sync_is_active_from_status(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(status="ACTIVE").update(is_active=True)
    User.objects.exclude(status="ACTIVE").update(is_active=False)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_alter_clientprofile_emergency_contact"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_active",
            field=models.BooleanField(default=True, verbose_name="계정 활성"),
        ),
        migrations.RunPython(sync_is_active_from_status, migrations.RunPython.noop),
    ]
