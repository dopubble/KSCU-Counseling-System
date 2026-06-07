import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.documents.models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SessionMaterial",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("title", models.CharField(blank=True, max_length=200, verbose_name="자료명")),
                (
                    "file",
                    models.FileField(
                        upload_to=apps.documents.models.session_material_upload_path,
                        verbose_name="파일",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "appointment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="materials",
                        to="scheduling.appointment",
                        verbose_name="예약(회기)",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="uploaded_session_materials",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="업로드한 사용자",
                    ),
                ),
            ],
            options={
                "verbose_name": "회기 자료",
                "verbose_name_plural": "회기 자료",
                "ordering": ["-created_at"],
            },
        ),
    ]
