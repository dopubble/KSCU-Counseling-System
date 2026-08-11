from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus


class CounselorListSearchTests(TestCase):
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
        self.http = Client()
        self.url = reverse("admin_panel:counselor_list")

    def test_list_without_search_shows_all_counselors(self):
        self.http.login(email="admin@example.com", password="pass12345")
        response = self.http.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "김창만")
        self.assertContains(response, "이영희")

    def test_search_filters_by_name_partial_match(self):
        self.http.login(email="admin@example.com", password="pass12345")
        response = self.http.get(self.url, {"search": "김"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "김창만")
        self.assertNotContains(response, "이영희")

    def test_search_combined_with_cohort_filter(self):
        self.http.login(email="admin@example.com", password="pass12345")
        response = self.http.get(self.url, {"cohort": "2", "search": "김"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "김창만")
        self.assertNotContains(response, "이영희")

    def test_search_with_no_results_shows_empty_message(self):
        self.http.login(email="admin@example.com", password="pass12345")
        response = self.http.get(self.url, {"search": "없는이름"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "조건에 맞는 상담사가 없습니다.")

    def test_csv_export_respects_search_filter(self):
        self.http.login(email="admin@example.com", password="pass12345")
        response = self.http.get(self.url, {"search": "김", "export": "csv"})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8-sig")
        self.assertIn("김창만", body)
        self.assertNotIn("이영희", body)
