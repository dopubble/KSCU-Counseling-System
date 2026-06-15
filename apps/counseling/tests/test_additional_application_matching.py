from django.test import Client as HttpClient, TestCase
from django.urls import reverse

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import ApplicationStatus, Case, CaseStatus, CounselingApplication
from apps.counseling.services import assign_counselor


def _create_client(email: str = "client@example.com") -> User:
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name="테스트내담자",
        role=UserRole.CLIENT,
        status=UserStatus.ACTIVE,
    )


def _create_counselor(email: str, name: str) -> User:
    user = User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=UserRole.COUNSELOR,
        status=UserStatus.ACTIVE,
    )
    CounselorProfile.objects.get_or_create(user=user, defaults={"cohort": 1})
    return user


def _create_application(client: User, *, reason: str) -> CounselingApplication:
    return CounselingApplication.objects.create(
        client=client,
        counseling_types=["개인상담"],
        reason=reason,
        status=ApplicationStatus.WAITING_MATCH,
    )


class AdditionalApplicationMatchingTests(TestCase):
    def setUp(self):
        self.http = HttpClient()
        self.client_user = _create_client()
        self.counselor_a = _create_counselor("counselor-a@example.com", "상담사A")
        self.counselor_b = _create_counselor("counselor-b@example.com", "상담사B")
        self.admin = User.objects.create_user(
            email="admin@example.com",
            password="pass12345",
            name="관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )

        self.first_application = _create_application(
            self.client_user,
            reason="첫 번째 상담 신청",
        )
        assign_counselor(self.first_application, self.counselor_a, total_sessions=8)

        self.second_application = _create_application(
            self.client_user,
            reason="다른 건 추가 상담 신청",
        )

    def test_admin_can_match_second_application_while_first_case_active(self):
        self.http.force_login(self.admin)
        url = reverse("counseling:application_detail", args=[self.second_application.pk])

        response = self.http.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "다른 건으로 진행 중인 상담")
        self.assertContains(response, "상담사 배정 완료")

        response = self.http.post(
            url,
            {
                "counselor": str(self.counselor_b.pk),
                "total_sessions": 10,
            },
        )
        self.assertEqual(response.status_code, 302)

        second_case = Case.objects.get(application=self.second_application)
        self.assertEqual(second_case.counselor_id, self.counselor_b.pk)
        self.assertEqual(second_case.status, CaseStatus.ACTIVE)
        self.assertEqual(
            Case.objects.filter(
                client=self.client_user,
                status=CaseStatus.ACTIVE,
                counselor__isnull=False,
            ).count(),
            2,
        )
        self.second_application.refresh_from_db()
        self.assertEqual(self.second_application.status, ApplicationStatus.IN_PROGRESS)
