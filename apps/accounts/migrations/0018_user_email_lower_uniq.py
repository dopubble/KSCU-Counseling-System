"""이메일을 대소문자 무시로도 유일하게 만든다.

기존 email unique=True 는 유지한다. 계정 행은 수정하거나 합치지 않는다.
제약을 만들기 직전에 대소문자 중복이 있으면 예외로 중단한다.
PostgreSQL에서는 이 마이그레이션 전체가 한 트랜잭션이라, 중단되면
제약 추가도 함께 되돌아간다.
"""

from django.db import migrations, models
from django.db.models import Count
from django.db.models.functions import Lower


def abort_if_email_case_duplicates(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    has_duplicates = (
        User.objects.annotate(email_key=Lower("email"))
        .values("email_key")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
        .exists()
    )
    if has_duplicates:
        raise RuntimeError(
            "대소문자만 다른 이메일이 있어 고유 제약을 추가하지 않습니다. "
            "기존 계정은 변경하지 않았습니다."
        )


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("accounts", "0017_alter_clientprofile_birth_date_and_more"),
    ]

    operations = [
        migrations.RunPython(
            abort_if_email_case_duplicates,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                Lower("email"),
                name="accounts_user_email_lower_uniq",
            ),
        ),
    ]
