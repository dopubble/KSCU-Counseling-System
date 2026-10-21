from django.test import SimpleTestCase

from kscu_counseling.settings.email_backend import (
    CONSOLE_EMAIL_BACKEND,
    RESEND_EMAIL_BACKEND,
    SMTP_EMAIL_BACKEND,
    select_production_email_backend,
)


class ProductionEmailBackendTests(SimpleTestCase):
    def test_resend_api_key_selects_https_backend(self):
        backend = select_production_email_backend(
            resend_api_key="re_test_key",
            email_host_user="sender@gmail.com",
            email_host_password="app-password",
        )
        self.assertEqual(backend, "anymail.backends.resend.EmailBackend")
        self.assertEqual(backend, RESEND_EMAIL_BACKEND)
        self.assertNotIn("smtp", backend)

    def test_smtp_credentials_do_not_override_resend(self):
        backend = select_production_email_backend(
            resend_api_key="re_test_key",
            email_host_user="sender@gmail.com",
            email_host_password="app-password",
        )
        self.assertNotEqual(backend, SMTP_EMAIL_BACKEND)

    def test_without_resend_key_smtp_credentials_still_use_smtp(self):
        backend = select_production_email_backend(
            resend_api_key="",
            email_host_user="sender@gmail.com",
            email_host_password="app-password",
        )
        self.assertEqual(backend, SMTP_EMAIL_BACKEND)

    def test_without_resend_or_smtp_uses_console(self):
        backend = select_production_email_backend(
            resend_api_key="   ",
            email_host_user="",
            email_host_password="",
        )
        self.assertEqual(backend, CONSOLE_EMAIL_BACKEND)
