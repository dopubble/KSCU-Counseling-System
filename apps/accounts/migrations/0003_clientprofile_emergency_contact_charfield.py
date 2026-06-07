# Generated manually — JSONField → CharField

from django.db import migrations, models


def _json_emergency_to_phone(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()[:20]
    if isinstance(value, dict):
        for key in ("phone", "tel", "mobile", "number", "contact"):
            raw = value.get(key)
            if raw and str(raw).strip():
                return str(raw).strip()[:20]
        for raw in value.values():
            if raw and str(raw).strip():
                return str(raw).strip()[:20]
    return str(value).strip()[:20]


def convert_emergency_contact_to_char(apps, schema_editor):
    ClientProfile = apps.get_model("accounts", "ClientProfile")
    for profile in ClientProfile.objects.all():
        profile.emergency_contact_phone = _json_emergency_to_phone(profile.emergency_contact)
        profile.save(update_fields=["emergency_contact_phone"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_clientprofile_student_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientprofile",
            name="emergency_contact_phone",
            field=models.CharField(
                blank=True,
                default="",
                max_length=20,
                verbose_name="비상연락처",
            ),
        ),
        migrations.RunPython(
            convert_emergency_contact_to_char,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="clientprofile",
            name="emergency_contact",
        ),
        migrations.RenameField(
            model_name="clientprofile",
            old_name="emergency_contact_phone",
            new_name="emergency_contact",
        ),
    ]
