"""기존 단일 상담 구분(session_category) → 목록 필드(session_categories) 이관."""

from django.db import migrations


def forwards(apps, schema_editor):
    CounselingJournal = apps.get_model("sessions_app", "CounselingJournal")
    updated = []
    for journal in CounselingJournal.objects.exclude(session_category="").iterator():
        if journal.session_categories:
            continue
        value = (journal.session_category or "").strip()
        if not value:
            continue
        journal.session_categories = [value]
        updated.append(journal)
    if updated:
        CounselingJournal.objects.bulk_update(updated, ["session_categories"])


class Migration(migrations.Migration):

    dependencies = [
        ("sessions_app", "0009_counselingjournal_session_categories"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
