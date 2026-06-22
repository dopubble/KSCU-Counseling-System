"""선리하(suneliha@mail.kcu.ac) 내담자 상담 방식: 대면 → 비대면."""

from django.db import connection, migrations

CLIENT_EMAIL = "suneliha@mail.kcu.ac"


def set_suneliha_remote(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    User = apps.get_model("accounts", "User")
    Case = apps.get_model("counseling", "Case")
    CounselingApplication = apps.get_model("counseling", "CounselingApplication")

    client = User.objects.filter(email__iexact=CLIENT_EMAIL, role="CLIENT").first()
    if not client:
        return

    CounselingApplication.objects.filter(client=client).update(counseling_method="REMOTE")
    Case.objects.filter(client=client).update(counseling_method="REMOTE")


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0034_counselingapplication_counseling_method"),
    ]

    operations = [
        migrations.RunPython(set_suneliha_remote, migrations.RunPython.noop),
    ]
