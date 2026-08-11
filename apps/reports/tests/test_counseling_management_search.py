from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import ApplicationStatus, CounselingApplication, CounselingMethod
from apps.counseling.services import assign_counselor


class CounselingManagementSearchTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@example.com",
            password="pass12345",
            name="관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        self.kim = User.objects.create_user(
            email="kim@example.com",
            password="pass12345",
            name="김창만",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        self.lee = User.objects.create_user(
            email="lee@example.com",
            password="pass12345",
            name="이영희",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.update_or_create(
            user=self.kim,
            defaults={"cohort": 2, "is_approved": True},
        )
        CounselorProfile.objects.update_or_create(
            user=self.lee,
            defaults={"cohort": 3, "is_approved": True},
        )
        self.client_kim = User.objects.create_user(
            email="client-kim@example.com",
            password="pass12345",
            name="내담자김",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.client_lee = User.objects.create_user(
            email="client-lee@example.com",
            password="pass12345",
            name="내담자이",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        kim_app = CounselingApplication.objects.create(
            client=self.client_kim,
            counseling_types=["개인상담"],
            reason="테스트",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.MATCHED,
        )
        lee_app = CounselingApplication.objects.create(
            client=self.client_lee,
            counseling_types=["개인상담"],
            reason="테스트",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.MATCHED,
        )
        self.kim_case = assign_counselor(kim_app, self.kim, total_sessions=10)
        self.lee_case = assign_counselor(lee_app, self.lee, total_sessions=10)
        self.http = Client()
        self.url = reverse("admin_panel:counseling_management")

    def test_active_tab_without_search_shows_all_cases(self):
        self.http.login(email="admin@example.com", password="pass12345")
        response = self.http.get(self.url, {"tab": "active"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "김창만")
        self.assertContains(response, "이영희")

    def test_active_tab_search_filters_by_counselor_name(self):
        self.http.login(email="admin@example.com", password="pass12345")
        response = self.http.get(self.url, {"tab": "active", "search": "김"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "김창만")
        self.assertNotContains(response, "이영희")

    def test_active_tab_search_filters_by_client_name(self):
        self.http.login(email="admin@example.com", password="pass12345")
        response = self.http.get(self.url, {"tab": "active", "search": "내담자이"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "이영희")
        self.assertNotContains(response, "김창만")

    def test_active_tab_search_with_no_results(self):
        self.http.login(email="admin@example.com", password="pass12345")
        response = self.http.get(self.url, {"tab": "active", "search": "없는이름"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "조건에 맞는 진행 중인 상담이 없습니다.")

    def test_search_input_preserved_in_response(self):
        self.http.login(email="admin@example.com", password="pass12345")
        response = self.http.get(self.url, {"tab": "active", "search": "김창만"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="김창만"')
