from django.db import migrations


def remove_orphan_counselor_profiles(apps, schema_editor):
    CounselorProfile = apps.get_model("accounts", "CounselorProfile")
    CounselorProfile.objects.exclude(user__role="COUNSELOR").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_counselorprofile_cohort"),
    ]

    operations = [
        migrations.RunPython(
            remove_orphan_counselor_profiles,
            migrations.RunPython.noop,
        ),
    ]
