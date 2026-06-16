"""김혜정 내담자 상담 방식: 대면 → 비대면."""

from django.db import migrations


def set_kim_hyejeong_remote(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Case = apps.get_model("counseling", "Case")

    client = User.objects.filter(name="김혜정", role="CLIENT").first()
    if not client:
        return

    Case.objects.filter(client=client, status="ACTIVE").update(counseling_method="REMOTE")


class Migration(migrations.Migration):
    dependencies = [
        ("counseling", "0025_full_reset_session1_matches_june2026"),
    ]

    operations = [
        migrations.RunPython(set_kim_hyejeong_remote, migrations.RunPython.noop),
    ]
