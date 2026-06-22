"""상담 신청서에 상담 방식(대면/비대면) 필드 추가."""

from django.db import migrations, models


def backfill_from_case(apps, schema_editor):
    Case = apps.get_model("counseling", "Case")
    CounselingApplication = apps.get_model("counseling", "CounselingApplication")

    for case in Case.objects.select_related("application").iterator():
        application = case.application
        if not application:
            continue
        method = (case.counseling_method or "").strip()
        if method:
            CounselingApplication.objects.filter(pk=application.pk).update(
                counseling_method=method
            )


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0033_student_test1_remote_counseling_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="counselingapplication",
            name="counseling_method",
            field=models.CharField(
                choices=[("IN_PERSON", "대면"), ("REMOTE", "비대면")],
                default="IN_PERSON",
                max_length=20,
                verbose_name="상담 방식",
            ),
        ),
        migrations.RunPython(backfill_from_case, migrations.RunPython.noop),
    ]
