from django.db import migrations, models


def backfill_assignment_cohort(apps, schema_editor):
    Assignment = apps.get_model("documents", "CounselorAssignmentSubmission")
    CounselorProfile = apps.get_model("accounts", "CounselorProfile")
    profiles = {
        p.user_id: p.cohort
        for p in CounselorProfile.objects.exclude(cohort__isnull=True).only("user_id", "cohort")
    }
    for assignment in Assignment.objects.filter(cohort__isnull=True).iterator():
        cohort = profiles.get(assignment.submitted_by_id)
        if cohort is not None:
            assignment.cohort = cohort
            assignment.save(update_fields=["cohort"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_counselorprofile_cohort"),
        ("documents", "0007_assignment_unique_per_case_session"),
    ]

    operations = [
        migrations.AddField(
            model_name="counselorassignmentsubmission",
            name="cohort",
            field=models.PositiveIntegerField(
                blank=True,
                db_index=True,
                help_text="제출 시 상담사 기수가 자동 저장됩니다.",
                null=True,
                verbose_name="기수",
            ),
        ),
        migrations.RunPython(backfill_assignment_cohort, migrations.RunPython.noop),
    ]
