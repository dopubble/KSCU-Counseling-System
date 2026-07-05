from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus


class LoginRedirectTests(TestCase):
    def setUp(self):
        self.counselor = User.objects.create_user(
            email="counselor@example.com",
            password="Testpass1234",
            name="상담사",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        profile = self.counselor.counselor_profile
        profile.is_approved = True
        profile.cohort = 1
        profile.save(update_fields=["is_approved", "cohort", "updated_at"])

        self.client_user = User.objects.create_user(
            email="client@example.com",
            password="Testpass1234",
            name="내담자",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )

    def test_counselor_login_without_next_goes_to_dashboard(self):
        client = Client()
        response = client.post(
            reverse("accounts:login"),
            {"username": self.counselor.email, "password": "Testpass1234"},
        )
        self.assertRedirects(
            response,
            reverse("counselor:dashboard"),
            fetch_redirect_response=False,
        )

    def test_counselor_login_with_foreign_next_goes_to_dashboard_not_403(self):
        client = Client()
        with self.assertLogs("apps.accounts.access", level="INFO") as logs:
            response = client.post(
                reverse("accounts:login"),
                {
                    "username": self.counselor.email,
                    "password": "Testpass1234",
                    "next": reverse("client:dashboard"),
                },
            )
        self.assertTrue(
            any("login next rejected by role" in record.message for record in logs.records),
            "역할 불일치 next 거부 로그가 기록되어야 합니다.",
        )
        self.assertRedirects(
            response,
            reverse("counselor:dashboard"),
            fetch_redirect_response=False,
        )

        follow = client.get(reverse("counselor:dashboard"))
        self.assertEqual(follow.status_code, 200)

    def test_permission_denied_logs_request_context(self):
        client = Client()
        client.force_login(self.client_user)
        with self.assertLogs("apps.accounts.access", level="WARNING") as logs:
            response = client.get(
                reverse("counselor:dashboard"),
                {"next": "/client/dashboard/"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            any("HTTP 403 permission denied" in record.message for record in logs.records),
        )

    def test_counselor_login_with_allowed_next_goes_to_target(self):
        target = reverse("counselor:presentation_board")
        client = Client()
        response = client.post(
            reverse("accounts:login"),
            {
                "username": self.counselor.email,
                "password": "Testpass1234",
                "next": target,
            },
        )
        self.assertRedirects(response, target, fetch_redirect_response=False)

    def test_client_login_with_counselor_next_goes_to_client_dashboard(self):
        client = Client()
        response = client.post(
            reverse("accounts:login"),
            {
                "username": self.client_user.email,
                "password": "Testpass1234",
                "next": reverse("counselor:dashboard"),
            },
        )
        self.assertRedirects(
            response,
            reverse("client:dashboard"),
            fetch_redirect_response=False,
        )
