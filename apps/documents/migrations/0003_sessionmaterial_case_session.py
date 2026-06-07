import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.documents.models


class Migration(migrations.Migration):

    dependencies = [
        ("counseling", "0004_case_session_fields"),
        ("documents", "0002_sessionmaterial"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionmaterial",
            name="case",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="session_materials",
                to="counseling.case",
                verbose_name="사례",
            ),
        ),
        migrations.AddField(
            model_name="sessionmaterial",
            name="session_number",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="회차"),
        ),
        migrations.AlterField(
            model_name="sessionmaterial",
            name="appointment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="materials",
                to="scheduling.appointment",
                verbose_name="예약(회기)",
            ),
        ),
    ]
