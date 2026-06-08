# Generated manually for assignment dedupe + unique constraint

from django.db import migrations, models


def dedupe_assignments(apps, schema_editor):
    """동일 사례·회차 중복 행 제거 — 최종 수정 시각 기준 최신 1건만 유지."""
    Model = apps.get_model("documents", "CounselorAssignmentSubmission")
    seen_keys: set = set()
    for row in Model.objects.all().order_by("-updated_at", "-created_at"):
        key = (row.case_id, row.session_number)
        if key in seen_keys:
            row.delete()
        else:
            seen_keys.add(key)


def fill_missing_session_numbers(apps, schema_editor):
    Model = apps.get_model("documents", "CounselorAssignmentSubmission")
    Model.objects.filter(session_number__isnull=True).update(session_number=1)


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0006_counselor_assignment_submission"),
    ]

    operations = [
        migrations.RunPython(dedupe_assignments, migrations.RunPython.noop),
        migrations.RunPython(fill_missing_session_numbers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="counselorassignmentsubmission",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, verbose_name="최초 제출일"),
        ),
        migrations.AlterField(
            model_name="counselorassignmentsubmission",
            name="session_number",
            field=models.PositiveIntegerField(
                help_text="과제가 해당하는 상담 회기.",
                verbose_name="회차",
            ),
        ),
        migrations.AlterField(
            model_name="counselorassignmentsubmission",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, verbose_name="최종 제출일"),
        ),
        migrations.AddConstraint(
            model_name="counselorassignmentsubmission",
            constraint=models.UniqueConstraint(
                fields=("case", "session_number"),
                name="unique_counselor_assignment_per_case_session",
            ),
        ),
    ]
