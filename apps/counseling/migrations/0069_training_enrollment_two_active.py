# 진행 중 수련 등록을 상담사당 1개에서 2개까지 허용한다.
# 기존 사례와 기록은 변경하지 않는다.

from django.db import migrations, models


def assign_existing_active_slots(apps, schema_editor):
    TrainingEnrollment = apps.get_model("counseling", "TrainingEnrollment")
    TrainingEnrollment.objects.filter(status="ACTIVE", active_slot__isnull=True).update(
        active_slot=1
    )


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0068_application_counseling_preference"),
    ]

    operations = [
        migrations.AddField(
            model_name="trainingenrollment",
            name="active_slot",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="진행 중일 때만 1 또는 2. 상담사당 진행 중 등록은 두 개까지다.",
                null=True,
                verbose_name="진행 슬롯",
            ),
        ),
        migrations.RunPython(assign_existing_active_slots, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="trainingenrollment",
            name="uniq_active_training_enrollment_per_counselor",
        ),
        migrations.AddConstraint(
            model_name="trainingenrollment",
            constraint=models.UniqueConstraint(
                fields=("counselor", "active_slot"),
                name="uniq_training_enrollment_counselor_active_slot",
            ),
        ),
        migrations.AddConstraint(
            model_name="trainingenrollment",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(status="ACTIVE", active_slot__in=[1, 2])
                    | models.Q(status="COMPLETED", active_slot__isnull=True)
                    | models.Q(status="WITHDRAWN", active_slot__isnull=True)
                ),
                name="training_enrollment_active_slot_matches_status",
            ),
        ),
    ]
