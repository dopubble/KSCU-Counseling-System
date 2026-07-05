from django.contrib.auth.hashers import check_password
from django.db import migrations

LEGACY_AUTO_PASSWORD = "260706"


def clear_auto_backfilled_password_hashes(apps, schema_editor):
    post_model = apps.get_model("counseling", "CasePresentationPost")
    comment_model = apps.get_model("counseling", "CasePresentationComment")

    for post in post_model.objects.exclude(file_password_hash=""):
        if check_password(LEGACY_AUTO_PASSWORD, post.file_password_hash):
            post.file_password_hash = ""
            post.save(update_fields=["file_password_hash"])

    for comment in comment_model.objects.exclude(file_password_hash=""):
        if check_password(LEGACY_AUTO_PASSWORD, comment.file_password_hash):
            comment.file_password_hash = ""
            comment.save(update_fields=["file_password_hash"])


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0057_backfill_presentation_legacy_file_password"),
    ]

    operations = [
        migrations.RunPython(
            clear_auto_backfilled_password_hashes,
            migrations.RunPython.noop,
        ),
    ]
