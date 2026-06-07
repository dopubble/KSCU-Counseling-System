from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0003_sessionmaterial_case_session"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionmaterial",
            name="is_shared",
            field=models.BooleanField(
                default=False,
                help_text="True면 상담 상세 '공유 자료실'에 노출되는 전체 공유용 파일입니다.",
                verbose_name="사례 공유 자료",
            ),
        ),
    ]
