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
            "agree_service_terms": True,
            "agree_privacy": True,
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

    def test_non_student_signup_stores_false_and_clears_department(self):
        data = self._signup_data("visitor@example.com")
        data["department"] = "저장되면안됨"
        data["student_id"] = "20110001"
        form = SignUpForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertFalse(user.client_profile.is_kcu_student)
        self.assertEqual(user.client_profile.department, "")
        self.assertEqual(user.client_profile.student_id, "20110001")

    def test_student_signup_requires_department_and_stores_true(self):
        missing = self._signup_data("student-missing@example.com")
        missing["is_kcu_student"] = "yes"
        missing["department"] = ""
        form = SignUpForm(data=missing)
        self.assertFalse(form.is_valid())
        self.assertIn("department", form.errors)

        data = self._signup_data("student@example.com")
        data["is_kcu_student"] = "yes"
        data["department"] = "상담심리학과"
        data["student_id"] = "20241234"
        form = SignUpForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertTrue(user.client_profile.is_kcu_student)
        self.assertEqual(user.client_profile.department, "상담심리학과")
        self.assertEqual(user.client_profile.student_id, "20241234")

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

    def test_consent_fields_required(self):
        data = self._signup_data("new@example.com")
        data["agree_service_terms"] = False
        data["agree_privacy"] = False
        form = SignUpForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("agree_service_terms", form.errors)
        self.assertIn("agree_privacy", form.errors)
