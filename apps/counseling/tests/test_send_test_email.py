from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


RESEND_BACKEND = "anymail.backends.resend.EmailBackend"
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


class SendTestEmailCommandTests(SimpleTestCase):
    @override_settings(
        EMAIL_BACKEND=RESEND_BACKEND,
        ANYMAIL={"RESEND_API_KEY": "re_test_key"},
        DEFAULT_FROM_EMAIL="sedulife@mail.kcu.ac",
        EMAIL_HOST_USER="",
        EMAIL_HOST_PASSWORD="",
    )
    @patch("apps.counseling.management.commands.send_test_email.send_mail")
    def test_resend_sends_without_smtp_password(self, send_mail):
        stdout = StringIO()
        stderr = StringIO()
        call_command("send_test_email", "person@example.com", stdout=stdout, stderr=stderr)
        send_mail.assert_called_once_with(
            subject="[KSCU 상담] 메일 발송 테스트",
            message="Resend HTTPS API 메일 발송 테스트입니다.",
            from_email="sedulife@mail.kcu.ac",
            recipient_list=["person@example.com"],
            fail_silently=False,
        )
        output = stdout.getvalue()
        self.assertIn("EMAIL_BACKEND = anymail.backends.resend.EmailBackend", output)
        self.assertIn("RESEND_API_KEY configured = YES", output)
        self.assertIn("DEFAULT_FROM_EMAIL = sedulife@mail.kcu.ac", output)
        self.assertIn("수신주소 = person@example.com", output)
        self.assertIn("발송 성공", output)
        self.assertNotIn("re_test_key", output)
        self.assertNotIn("re_test_key", stderr.getvalue())

    @override_settings(
        EMAIL_BACKEND=SMTP_BACKEND,
        ANYMAIL={},
        EMAIL_HOST_USER="",
        EMAIL_HOST_PASSWORD="",
    )
    @patch("apps.counseling.management.commands.send_test_email.send_mail")
    def test_smtp_still_requires_host_credentials(self, send_mail):
        stdout = StringIO()
        stderr = StringIO()
        call_command("send_test_email", "person@example.com", stdout=stdout, stderr=stderr)
        send_mail.assert_not_called()
        self.assertIn("EMAIL_HOST_USER", stderr.getvalue())
