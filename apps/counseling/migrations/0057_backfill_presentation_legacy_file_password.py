from django.contrib.auth.hashers import make_password
from django.db import migrations

LEGACY_DOWNLOAD_PASSWORD = "260706"


def backfill_legacy_presentation_file_passwords(apps, schema_editor):
    legacy_hash = make_password(LEGACY_DOWNLOAD_PASSWORD)
    post_model = apps.get_model("counseling", "CasePresentationPost")
    comment_model = apps.get_model("counseling", "CasePresentationComment")

    post_model.objects.filter(file_password_hash="").exclude(file="").update(
        file_password_hash=legacy_hash
    )
    comment_model.objects.filter(file_password_hash="").exclude(file="").update(
        file_password_hash=legacy_hash
    )


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0056_presentation_file_password_hash"),
    ]

    operations = [
        migrations.RunPython(
            backfill_legacy_presentation_file_passwords,
            migrations.RunPython.noop,
        ),
    ]
