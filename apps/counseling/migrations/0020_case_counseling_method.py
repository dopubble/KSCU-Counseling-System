from django.db import migrations, models

REMOTE_CLIENT_NAMES = {
    "서영진",
    "고혜숙",
    "안정민",
    "이경숙",
    "김수미",
    "이현옥",
    "정경화",
    "구현정",
    "정진아",
    "박미영",
    "홍연서",
    "정한결",
    "김효순",
    "조선혜",
    "조영은",
    "임유정",
    "오유진",
    "조현경",
}


def apply_counseling_methods(apps, schema_editor):
    Case = apps.get_model("counseling", "Case")
    User = apps.get_model("accounts", "User")

    remote_client_ids = list(
        User.objects.filter(name__in=REMOTE_CLIENT_NAMES).values_list("pk", flat=True)
    )
    if remote_client_ids:
        Case.objects.filter(client_id__in=remote_client_ids).update(
            counseling_method="REMOTE"
        )
    Case.objects.exclude(client_id__in=remote_client_ids).update(
        counseling_method="IN_PERSON"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0019_counselingapplication_profile_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="counseling_method",
            field=models.CharField(
                choices=[("IN_PERSON", "대면"), ("REMOTE", "비대면")],
                default="IN_PERSON",
                max_length=20,
                verbose_name="상담 진행 방식",
            ),
        ),
        migrations.RunPython(apply_counseling_methods, migrations.RunPython.noop),
    ]
