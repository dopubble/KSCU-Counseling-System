"""관리자 — 사례발표 case=NULL 레거시 수동 연결."""

from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import Client as HttpClient, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CasePresentationPost,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.counseling.privacy import mask_client_name
from apps.counseling.training_progress import get_counselor_training_progress


def _user(email, name, role):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


def _counselor(name, cohort=1):
    user = _user(f"{name}@example.com", name, UserRole.COUNSELOR)
    CounselorProfile.objects.update_or_create(
        user=user,
        defaults={"cohort": cohort, "is_approved": True},
    )
    return user


def _client_user(name):
    return _user(f"{name}@client.example.com", name, UserRole.CLIENT)


def _case(counselor, client, case_number, *, status=CaseStatus.ACTIVE):
    application = CounselingApplication.objects.create(
        client=client,
        counseling_types=["진로상담"],
        reason="test",
        counseling_method=CounselingMethod.IN_PERSON,
        status=ApplicationStatus.IN_PROGRESS,
    )
    return Case.objects.create(
        application=application,
        client=client,
        counselor=counselor,
        case_number=case_number,
        status=status,
        counseling_method=CounselingMethod.IN_PERSON,
        total_sessions=10,
        remaining_sessions=10,
    )


def _post(author, title, *, case=None, cohort=1):
    return CasePresentationPost.objects.create(
        cohort=cohort,
        author=author,
        case=case,
        title=title,
        file=SimpleUploadedFile(f"{title}.pdf", b"%PDF-1.4"),
    )


class AdminUnlinkedPresentationListTests(TestCase):
    def setUp(self):
        self.admin = _user("admin-ulink@example.com", "관리자", UserRole.ADMIN)
        self.counselor_a = _counselor("연결상담A")
        self.counselor_b = _counselor("연결상담B")
        self.client_a = _client_user("김내담자")
        self.client_b = _client_user("이내담자")
        self.case_a = _case(self.counselor_a, self.client_a, "CASE-UL-A1")
        self.case_b = _case(self.counselor_b, self.client_b, "CASE-UL-B1")
        self.unlinked = _post(self.counselor_a, "미연결발표")
        self.linked = _post(self.counselor_a, "이미연결", case=self.case_a)
        self.list_url = reverse("admin_panel:unlinked_case_presentations")
        self.http = HttpClient()
        self.http.force_login(self.admin)

    def test_unlinked_post_appears_in_admin_list(self):
        response = self.http.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "미연결발표")
        self.assertContains(response, "연결상담A")
        self.assertContains(response, "미연결 사례발표 게시글 1건")
        detail_url = reverse(
            "counselor:presentation_board_detail", args=[self.unlinked.pk]
        )
        self.assertContains(response, detail_url)

    def test_linked_post_excluded_from_list(self):
        response = self.http.get(self.list_url)
        self.assertNotContains(response, "이미연결")

    def test_select_shows_only_author_cases(self):
        response = self.http.get(self.list_url)
        self.assertContains(response, "CASE-UL-A1")
        self.assertContains(
            response,
            f"CASE-UL-A1 · {mask_client_name(self.client_a.name)} · 개입중",
        )
        self.assertNotContains(response, "CASE-UL-B1")
        self.assertNotContains(response, str(self.case_b.pk))

    def test_list_avoids_per_post_case_queries(self):
        for index in range(5):
            author = _counselor(f"다수상담{index}")
            client = _client_user(f"다수내담{index}")
            _case(author, client, f"CASE-UL-M{index}")
            _post(author, f"다수미연결{index}")

        with CaptureQueriesContext(connection) as ctx:
            response = self.http.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        case_table = Case._meta.db_table
        case_selects = [
            q["sql"]
            for q in ctx.captured_queries
            if q["sql"].startswith("SELECT") and case_table in q["sql"]
        ]
        self.assertLessEqual(len(case_selects), 2)


class AdminUnlinkedPresentationLinkTests(TestCase):
    def setUp(self):
        self.admin = _user("admin-link@example.com", "관리자", UserRole.ADMIN)
        self.counselor_a = _counselor("저장상담A")
        self.counselor_b = _counselor("저장상담B")
        self.client_a1 = _client_user("저장내담1")
        self.client_a2 = _client_user("저장내담2")
        self.client_b = _client_user("저장내담B")
        self.case_a1 = _case(self.counselor_a, self.client_a1, "CASE-SV-A1")
        self.case_a2 = _case(self.counselor_a, self.client_a2, "CASE-SV-A2")
        self.case_b = _case(self.counselor_b, self.client_b, "CASE-SV-B1")
        self.unlinked = _post(self.counselor_a, "연결대상")
        self.list_url = reverse("admin_panel:unlinked_case_presentations")
        self.link_url = reverse(
            "admin_panel:unlinked_case_presentation_link",
            args=[self.unlinked.pk],
        )
        self.http = HttpClient()
        self.http.force_login(self.admin)

    def test_admin_links_author_case_and_updates_progress(self):
        before = get_counselor_training_progress(self.counselor_a)
        self.assertEqual(before["case_report_required_count"], 2)
        self.assertEqual(before["case_report_submitted_count"], 0)

        response = self.http.post(self.link_url, {"case": str(self.case_a1.pk)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.list_url)

        self.unlinked.refresh_from_db()
        self.assertEqual(self.unlinked.case_id, self.case_a1.pk)

        after = get_counselor_training_progress(self.counselor_a)
        self.assertEqual(after["case_report_required_count"], 2)
        self.assertEqual(after["case_report_submitted_count"], 1)

        listed = self.http.get(self.list_url)
        self.assertNotContains(listed, "연결대상")
        self.assertContains(listed, "미연결 사례발표 게시글이 없습니다.")

    def test_rejects_other_counselor_case(self):
        response = self.http.post(self.link_url, {"case": str(self.case_b.pk)})
        self.assertEqual(response.status_code, 302)
        self.unlinked.refresh_from_db()
        self.assertIsNone(self.unlinked.case_id)
        progress = get_counselor_training_progress(self.counselor_a)
        self.assertEqual(progress["case_report_submitted_count"], 0)

    def test_rejects_empty_case_selection(self):
        response = self.http.post(self.link_url, {"case": ""})
        self.assertEqual(response.status_code, 302)
        self.unlinked.refresh_from_db()
        self.assertIsNone(self.unlinked.case_id)

    def test_does_not_overwrite_already_linked_post(self):
        already = _post(self.counselor_a, "기연결", case=self.case_a1)
        url = reverse(
            "admin_panel:unlinked_case_presentation_link",
            args=[already.pk],
        )
        response = self.http.post(url, {"case": str(self.case_a2.pk)})
        self.assertEqual(response.status_code, 302)
        already.refresh_from_db()
        self.assertEqual(already.case_id, self.case_a1.pk)

    def test_distinct_case_count_after_two_links(self):
        first = self.unlinked
        second = _post(self.counselor_a, "두번째미연결")

        self.http.post(
            reverse(
                "admin_panel:unlinked_case_presentation_link",
                args=[first.pk],
            ),
            {"case": str(self.case_a1.pk)},
        )
        progress = get_counselor_training_progress(self.counselor_a)
        self.assertEqual(progress["case_report_submitted_count"], 1)

        self.http.post(
            reverse(
                "admin_panel:unlinked_case_presentation_link",
                args=[second.pk],
            ),
            {"case": str(self.case_a1.pk)},
        )
        same_case = get_counselor_training_progress(self.counselor_a)
        self.assertEqual(same_case["case_report_submitted_count"], 1)

        third = _post(self.counselor_a, "세번째미연결")
        self.http.post(
            reverse(
                "admin_panel:unlinked_case_presentation_link",
                args=[third.pk],
            ),
            {"case": str(self.case_a2.pk)},
        )
        two_cases = get_counselor_training_progress(self.counselor_a)
        self.assertEqual(two_cases["case_report_submitted_count"], 2)

    def test_audit_command_reflects_link(self):
        out = StringIO()
        call_command("audit_presentation_case_links", stdout=out)
        self.assertIn("posts_case_null=1", out.getvalue())

        self.http.post(self.link_url, {"case": str(self.case_a1.pk)})

        out2 = StringIO()
        call_command("audit_presentation_case_links", stdout=out2)
        body = out2.getvalue()
        self.assertIn("posts_case_linked=1", body)
        self.assertIn("posts_case_null=0", body)


class AdminUnlinkedPresentationAccessTests(TestCase):
    def setUp(self):
        self.list_url = reverse("admin_panel:unlinked_case_presentations")
        self.counselor = _counselor("권한상담")
        self.client_user = _client_user("권한내담")
        self.case = _case(self.counselor, self.client_user, "CASE-AC-1")
        self.post = _post(self.counselor, "권한미연결")
        self.link_url = reverse(
            "admin_panel:unlinked_case_presentation_link",
            args=[self.post.pk],
        )

    def _assert_blocked(self, role, name):
        user = _user(f"{name}@example.com", name, role)
        if role == UserRole.COUNSELOR:
            CounselorProfile.objects.update_or_create(
                user=user, defaults={"cohort": 1, "is_approved": True}
            )
        http = HttpClient()
        http.force_login(user)
        self.assertEqual(http.get(self.list_url).status_code, 403)
        self.assertEqual(
            http.post(self.link_url, {"case": str(self.case.pk)}).status_code,
            403,
        )
        self.post.refresh_from_db()
        self.assertIsNone(self.post.case_id)

    def test_counselor_blocked(self):
        self._assert_blocked(UserRole.COUNSELOR, "차단상담")

    def test_supervisor_blocked(self):
        self._assert_blocked(UserRole.SUPERVISOR, "차단수퍼")

    def test_client_blocked(self):
        self._assert_blocked(UserRole.CLIENT, "차단내담")

    def test_anonymous_redirects_to_login(self):
        http = HttpClient()
        response = http.get(self.list_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)


class AdminUnlinkedPresentationEntryPointsTests(TestCase):
    def setUp(self):
        self.admin = _user("admin-entry@example.com", "관리자", UserRole.ADMIN)
        self.counselor = _counselor("진입상담")
        _post(self.counselor, "진입미연결")
        self.board_url = reverse("counselor:presentation_board")
        self.unlinked_url = reverse("admin_panel:unlinked_case_presentations")
        self.http = HttpClient()
        self.http.force_login(self.admin)

    def test_dashboard_shows_unlinked_alert(self):
        response = self.http.get(reverse("admin_panel:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Case 미연결 사례발표 게시글 1건")
        self.assertContains(response, self.unlinked_url)

    def test_presentation_board_shows_admin_entry(self):
        response = self.http.get(self.board_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["show_admin_unlinked_presentation_banner"])
        self.assertContains(response, "Case 미연결 게시글 1건")
        self.assertContains(response, "미연결 사례 연결")
        self.assertContains(response, self.unlinked_url)

    def test_supervisor_presentation_board_hides_admin_banner(self):
        supervisor = _user("entry-super@example.com", "진입수퍼", UserRole.SUPERVISOR)
        http = HttpClient()
        http.force_login(supervisor)
        response = http.get(self.board_url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["show_admin_unlinked_presentation_banner"])
        self.assertNotContains(response, "미연결 사례 연결")
        self.assertNotContains(response, self.unlinked_url)

    def test_counselor_presentation_board_hides_admin_banner(self):
        http = HttpClient()
        http.force_login(self.counselor)
        response = http.get(self.board_url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["show_admin_unlinked_presentation_banner"])
        self.assertNotContains(response, "미연결 사례 연결")
        self.assertNotContains(response, self.unlinked_url)
