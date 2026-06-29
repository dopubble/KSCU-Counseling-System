"""동의서 스토리지가 레거시 미디어와 분리되는지 검증."""

import tempfile

from django.core.files.storage import FileSystemStorage
from django.test import TestCase, override_settings

from apps.documents.models import ConsentDocument
from kscu_counseling.settings.base import _env_str
from kscu_counseling.settings.consent_storage import apply_consent_storage
from kscu_counseling.settings.media_storage import apply_legacy_media_storage


class ConsentStorageIsolationTests(TestCase):
    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "consent": {"BACKEND": "apps.documents.storage.ConsentMediaStorage", "OPTIONS": {}},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
        MEDIA_ROOT=tempfile.mkdtemp(),
    )
    def test_consent_field_resolves_consent_storage(self):
        from apps.documents.storage import ConsentMediaStorage

        field = ConsentDocument._meta.get_field("file")
        storage = field.storage
        self.assertIsInstance(storage, ConsentMediaStorage)

    def test_aws_vars_without_media_use_s3_do_not_switch_legacy(self):
        def fake_env(name, default=""):
            values = {
                "AWS_STORAGE_BUCKET_NAME": "legacy-bucket",
                "AWS_ACCESS_KEY_ID": "key",
                "AWS_SECRET_ACCESS_KEY": "secret",
            }
            return values.get(name, default)

        module = {}
        mode = apply_legacy_media_storage(module, env_str=fake_env, on_railway=False)
        self.assertEqual(mode, "ephemeral")
        self.assertEqual(
            module["STORAGES"]["default"]["BACKEND"],
            "django.core.files.storage.FileSystemStorage",
        )

    def test_consent_aws_vars_configure_isolated_consent_backend(self):
        def fake_env(name, default=""):
            values = {
                "CONSENT_AWS_STORAGE_BUCKET_NAME": "consent-only-bucket",
                "CONSENT_AWS_ACCESS_KEY_ID": "ckey",
                "CONSENT_AWS_SECRET_ACCESS_KEY": "csecret",
                "CONSENT_AWS_S3_REGION_NAME": "ap-northeast-2",
            }
            return values.get(name, default)

        module = {"INSTALLED_APPS": []}
        apply_legacy_media_storage(module, env_str=lambda n, d="": "", on_railway=False)
        mode = apply_consent_storage(module, env_str=fake_env, on_railway=False)
        self.assertEqual(mode, "s3")
        self.assertEqual(
            module["STORAGES"]["default"]["BACKEND"],
            "django.core.files.storage.FileSystemStorage",
        )
        self.assertEqual(
            module["STORAGES"]["consent"]["BACKEND"],
            "apps.documents.storage.ConsentMediaStorage",
        )
