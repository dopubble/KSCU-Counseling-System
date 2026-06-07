import apps.documents.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0004_sessionmaterial_is_shared"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionmaterial",
            name="content",
            field=models.TextField(blank=True, verbose_name="내용"),
        ),
        migrations.AddField(
            model_name="sessionmaterial",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, verbose_name="수정일"),
        ),
        migrations.AlterField(
            model_name="sessionmaterial",
            name="file",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to=apps.documents.models.session_material_upload_path,
                verbose_name="파일",
            ),
        ),
        migrations.AlterField(
            model_name="sessionmaterial",
            name="is_shared",
            field=models.BooleanField(
                default=False,
                help_text="True면 상담 상세 '게시판'에 노출되는 전체 공유용 게시글입니다.",
                verbose_name="사례 공유 자료",
            ),
        ),
        migrations.AlterField(
            model_name="sessionmaterial",
            name="title",
            field=models.CharField(blank=True, max_length=200, verbose_name="제목"),
        ),
    ]
