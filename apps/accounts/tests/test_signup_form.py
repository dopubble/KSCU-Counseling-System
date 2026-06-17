from django.test import TestCase

from apps.accounts.forms import SignUpForm
from apps.accounts.models import User, UserRole, UserStatus


class SignUpFormEmailTests(TestCase):
    def setUp(self):
        User.objects.create_user(
            email="existing@example.com",
            password="Testpass123!",
            name="기존",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )

    def _signup_data(self, email: str) -> dict:
        return {
            "email": email,
            "name": "신규",
            "phone": "010-1234-5678",
            "role": UserRole.CLIENT,
            "password1": "Testpass123!",
            "password2": "Testpass123!",
            "agree_terms": True,
            "birth_date": "1990-01-01",
            "is_kcu_student": "no",
        }

    def test_duplicate_email_rejected_with_friendly_message(self):
        form = SignUpForm(data=self._signup_data("existing@example.com"))
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["email"], ["이미 가입된 이메일입니다."])

    def test_duplicate_email_rejected_case_insensitive(self):
        form = SignUpForm(data=self._signup_data("EXISTING@example.com"))
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["email"], ["이미 가입된 이메일입니다."])

    def test_new_email_allowed(self):
        form = SignUpForm(data=self._signup_data("new@example.com"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_phone_required(self):
        data = self._signup_data("new@example.com")
        data["phone"] = ""
        form = SignUpForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("phone", form.errors)

    def test_phone_whitespace_only_rejected(self):
        data = self._signup_data("new@example.com")
        data["phone"] = "   "
        form = SignUpForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["phone"], ["휴대폰 번호를 입력해 주세요."])
