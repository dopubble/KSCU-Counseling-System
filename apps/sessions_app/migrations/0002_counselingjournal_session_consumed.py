from django.db import migrations, models


def mark_existing_completed_journals_consumed(apps, schema_editor):
    CounselingJournal = apps.get_model("sessions_app", "CounselingJournal")
    CounselingJournal.objects.filter(is_draft=False).update(session_consumed=True)


class Migration(migrations.Migration):

    dependencies = [
        ("sessions_app", "0001_initial"),
        ("counseling", "0004_case_session_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="counselingjournal",
            name="session_consumed",
            field=models.BooleanField(
                default=False,
                help_text="완료된 일지가 사례의 남은 회기에 반영되었는지",
                verbose_name="회기 차감 완료",
            ),
        ),
        migrations.RunPython(
            mark_existing_completed_journals_consumed,
            migrations.RunPython.noop,
        ),
    ]
