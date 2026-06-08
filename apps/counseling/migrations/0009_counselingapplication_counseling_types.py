from django.db import migrations, models


VALID_TYPES = {
    "진로상담",
    "개인성격",
    "대인관계",
    "부부관계",
    "자녀관계",
}

LEGACY_MAP = {
    "개인상담": "개인성격",
    "학업상담": "진로상담",
    "가족상담": "자녀관계",
    "심리·정서": "개인성격",
    "기타": "개인성격",
}


def _migrate_legacy(value: str) -> list[str]:
    text = (value or "").strip()
    if not text:
        return []
    if text in VALID_TYPES:
        return [text]
    if text in LEGACY_MAP:
        return [LEGACY_MAP[text]]
    if "|" in text:
        category = text.split("|", 1)[0].strip()
        if category in VALID_TYPES:
            return [category]
    return []


def forwards_copy_counseling_types(apps, schema_editor):
    CounselingApplication = apps.get_model("counseling", "CounselingApplication")
    for application in CounselingApplication.objects.all().iterator():
        legacy = getattr(application, "counseling_type", "") or ""
        application.counseling_types = _migrate_legacy(legacy)
        application.save(update_fields=["counseling_types"])


def backwards_copy_counseling_type(apps, schema_editor):
    CounselingApplication = apps.get_model("counseling", "CounselingApplication")
    for application in CounselingApplication.objects.all().iterator():
        types = application.counseling_types or []
        application.counseling_type = ", ".join(types)
        application.save(update_fields=["counseling_type"])


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0008_sessionschedulechangerequest_message_blank"),
    ]

    operations = [
        migrations.AddField(
            model_name="counselingapplication",
            name="counseling_types",
            field=models.JSONField(blank=True, default=list, verbose_name="상담 유형"),
        ),
        migrations.RunPython(
            forwards_copy_counseling_types,
            backwards_copy_counseling_type,
        ),
        migrations.RemoveField(
            model_name="counselingapplication",
            name="counseling_type",
        ),
    ]
