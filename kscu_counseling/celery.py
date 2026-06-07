"""KSCU Counseling System — Celery application."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "kscu_counseling.settings.development")

app = Celery("kscu_counseling")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
