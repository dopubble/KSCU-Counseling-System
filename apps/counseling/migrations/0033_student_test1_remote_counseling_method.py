"""학생테스트1 내담자 상담 방식: 대면 → 비대면."""

from django.db import connection, migrations


def set_student_test1_remote(apps, schema_editor):
    engine = connection.settings_dict.get("ENGINE", "")
    if "sqlite" in engine:
        return

    User = apps.get_model("accounts", "User")
    Case = apps.get_model("counseling", "Case")

    client = User.objects.filter(name="학생테스트1", role="CLIENT").first()
    if not client:
        return

    Case.objects.filter(client=client).update(counseling_method="REMOTE")


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0032_ops_production_fixup_june2026_retry"),
    ]

    operations = [
        migrations.RunPython(set_student_test1_remote, migrations.RunPython.noop),
    ]
