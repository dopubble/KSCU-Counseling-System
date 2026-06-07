from django.db import migrations, models


def sync_remaining_sessions_from_journals(apps, schema_editor):
    Case = apps.get_model("counseling", "Case")
    CounselingJournal = apps.get_model("sessions_app", "CounselingJournal")

    Application = apps.get_model("counseling", "CounselingApplication")

    for case in Case.objects.all():
        consumed = CounselingJournal.objects.filter(
            case_id=case.pk,
            is_draft=False,
        ).count()
        remaining = max(0, case.total_sessions - consumed)
        case.remaining_sessions = remaining
        if remaining == 0 and case.status == "ACTIVE":
            case.status = "CLOSED"
        case.save(update_fields=["remaining_sessions", "status"])
        if remaining == 0:
            application = Application.objects.filter(pk=case.application_id).first()
            if application and application.status not in ("CANCELLED", "CLOSED"):
                application.status = "CLOSED"
                application.save(update_fields=["status"])


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0003_alter_counselingapplication_status"),
        ("sessions_app", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="total_sessions",
            field=models.PositiveIntegerField(
                default=10,
                help_text="상담사 매칭 시 설정하는 전체 상담 회기 수",
                verbose_name="총 회기 수",
            ),
        ),
        migrations.AddField(
            model_name="case",
            name="remaining_sessions",
            field=models.PositiveIntegerField(
                default=10,
                help_text="상담일지 완료 시 1회씩 차감됩니다.",
                verbose_name="남은 회기 수",
            ),
        ),
        migrations.RunPython(
            sync_remaining_sessions_from_journals,
            migrations.RunPython.noop,
        ),
    ]
