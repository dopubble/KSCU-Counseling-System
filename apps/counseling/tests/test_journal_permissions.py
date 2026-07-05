"""상담일지 열람 권한 — 동기 열람 차단·수퍼바이저 기수 범위."""

from django.test import Client as HttpClient, TestCase
from django.urls import reverse

from apps.accounts.models import (
    CounselorProfile,
    SupervisorProfile,
    User,
    UserRole,
    UserStatus,
)
from apps.counseling.journal_permissions import (
    user_can_download_initial_counseling_record_pdf,
    user_can_download_journal_pdf,
    user_can_view_initial_counseling_record,
    user_can_view_journal,
)
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.sessions_app.models import CounselingJournal, InitialCounselingRecord


def _create_client(name: str = "내담자") -> User:
    return User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.CLIENT,
        status=UserStatus.ACTIVE,
    )


def _create_counselor(name: str, cohort: int) -> User:
    user = User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.COUNSELOR,
        status=UserStatus.ACTIVE,
    )
    CounselorProfile.objects.update_or_create(
        user=user,
        defaults={"cohort": cohort, "is_approved": True},
    )
    return user


def _create_case(counselor: User, client: User, case_number: str) -> Case:
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
        status=CaseStatus.ACTIVE,
        counseling_method=CounselingMethod.IN_PERSON,
        total_sessions=8,
        remaining_sessions=8,
    )


def _create_journal(case: Case, counselor: User, session_number: int = 1) -> CounselingJournal:
    return CounselingJournal.objects.create(
        case=case,
        counselor=counselor,
        session_number=session_number,
        is_draft=False,
        subjective="S",
        objective="O",
        assessment="A",
        plan="P",
    )


def _create_initial_record(case: Case, counselor: User) -> InitialCounselingRecord:
    return InitialCounselingRecord.objects.create(
        case=case,
        counselor=counselor,
        is_draft=False,
        presented_problems_summary="호소 내용",
        clinical_strategy="전략",
    )


class JournalPermissionTests(TestCase):
    def setUp(self):
        self.peer_a = _create_counselor("상담사A", cohort=1)
        self.peer_b = _create_counselor("상담사B", cohort=1)
        self.other_cohort = _create_counselor("상담사C", cohort=2)
        self.client_a = _create_client("내담자A")
        self.client_b = _create_client("내담자B")
        self.case_a = _create_case(self.peer_a, self.client_a, "CASE-JP-1")
        self.case_b = _create_case(self.peer_b, self.client_b, "CASE-JP-2")
        self.journal_a = _create_journal(self.case_a, self.peer_a)
        self.journal_b = _create_journal(self.case_b, self.peer_b)

    def test_counselor_cannot_view_peer_journal(self):
        self.assertFalse(user_can_view_journal(self.peer_a, self.journal_b))
        self.assertFalse(user_can_download_journal_pdf(self.peer_a, self.journal_b))

    def test_author_can_view_own_journal(self):
        self.assertTrue(user_can_view_journal(self.peer_a, self.journal_a))

    def test_supervisor_assigned_cohort_can_view_peer_journal(self):
        supervisor = User.objects.create_user(
            email="supervisor@example.com",
            password="pass12345",
            name="수퍼바이저",
            role=UserRole.SUPERVISOR,
            status=UserStatus.ACTIVE,
        )
        SupervisorProfile.objects.update_or_create(
            user=supervisor, defaults={"assigned_cohorts": [1]}
        )
        self.assertTrue(user_can_view_journal(supervisor, self.journal_b))
        self.assertFalse(user_can_view_journal(supervisor, _create_journal(
            _create_case(self.other_cohort, _create_client("내담자C"), "CASE-JP-3"),
            self.other_cohort,
        )))

    def test_admin_can_view_any_journal(self):
        admin = User.objects.create_user(
            email="admin@example.com",
            password="pass12345",
            name="관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        self.assertTrue(user_can_view_journal(admin, self.journal_b))

    def test_counselor_cohort_journal_pdf_endpoint_blocked(self):
        http = HttpClient()
        http.force_login(self.peer_a)
        url = reverse("counselor:cohort_journal_pdf", kwargs={"journal_pk": self.journal_b.pk})
        response = http.post(url, {"pdf_password": "test1234"})
        self.assertEqual(response.status_code, 403)

    def test_supervisor_can_access_cohort_journals_page(self):
        supervisor = User.objects.create_user(
            email="supervisor2@example.com",
            password="pass12345",
            name="수퍼바이저2",
            role=UserRole.SUPERVISOR,
            status=UserStatus.ACTIVE,
        )
        SupervisorProfile.objects.update_or_create(
            user=supervisor, defaults={"assigned_cohorts": [1]}
        )
        http = HttpClient()
        http.force_login(supervisor)
        response = http.get(reverse("supervisor:cohort_journals"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CASE-JP-1")
        self.assertContains(response, "CASE-JP-2")

    def test_supervisor_other_cohort_journal_pdf_denied(self):
        journal_other = _create_journal(
            _create_case(self.other_cohort, _create_client("내담자D"), "CASE-JP-4"),
            self.other_cohort,
        )
        supervisor = User.objects.create_user(
            email="supervisor3@example.com",
            password="pass12345",
            name="수퍼바이저3",
            role=UserRole.SUPERVISOR,
            status=UserStatus.ACTIVE,
        )
        SupervisorProfile.objects.update_or_create(
            user=supervisor, defaults={"assigned_cohorts": [1]}
        )
        http = HttpClient()
        http.force_login(supervisor)
        url = reverse("supervisor:journal_pdf", kwargs={"journal_pk": journal_other.pk})
        response = http.post(url, {"pdf_password": "test1234"})
        self.assertEqual(response.status_code, 403)


class InitialRecordSupervisorTests(TestCase):
    def setUp(self):
        self.peer_a = _create_counselor("상담사A", cohort=1)
        self.peer_b = _create_counselor("상담사B", cohort=1)
        self.other_cohort = _create_counselor("상담사C", cohort=2)
        self.case_a = _create_case(self.peer_a, _create_client("내담자A"), "CASE-IR-1")
        self.case_b = _create_case(self.peer_b, _create_client("내담자B"), "CASE-IR-2")
        self.record_a = _create_initial_record(self.case_a, self.peer_a)
        self.record_b = _create_initial_record(self.case_b, self.peer_b)
        self.supervisor = User.objects.create_user(
            email="supervisor-ir@example.com",
            password="pass12345",
            name="수퍼바이저",
            role=UserRole.SUPERVISOR,
            status=UserStatus.ACTIVE,
        )
        SupervisorProfile.objects.update_or_create(
            user=self.supervisor,
            defaults={"assigned_cohorts": [1]},
        )

    def test_counselor_cannot_view_peer_initial_record(self):
        self.assertFalse(
            user_can_view_initial_counseling_record(self.peer_a, self.record_b)
        )

    def test_supervisor_assigned_cohort_can_view_initial_record(self):
        self.assertTrue(
            user_can_view_initial_counseling_record(self.supervisor, self.record_b)
        )
        self.assertTrue(
            user_can_download_initial_counseling_record_pdf(
                self.supervisor, self.record_b
            )
        )

    def test_supervisor_other_cohort_initial_record_denied(self):
        other_case = _create_case(
            self.other_cohort,
            _create_client("내담자C"),
            "CASE-IR-3",
        )
        other_record = _create_initial_record(other_case, self.other_cohort)
        self.assertFalse(
            user_can_view_initial_counseling_record(self.supervisor, other_record)
        )

    def test_supervisor_can_access_initial_records_page(self):
        http = HttpClient()
        http.force_login(self.supervisor)
        response = http.get(reverse("supervisor:cohort_initial_records"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CASE-IR-1")
        self.assertContains(response, "CASE-IR-2")
        self.assertNotContains(response, "CASE-IR-3")

    def test_supervisor_can_view_initial_record_detail(self):
        http = HttpClient()
        http.force_login(self.supervisor)
        response = http.get(
            reverse(
                "supervisor:initial_record_detail",
                kwargs={"record_pk": self.record_b.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "호소 내용")
        self.assertContains(response, "전략")

    def test_supervisor_dashboard_shows_initial_records_link(self):
        http = HttpClient()
        http.force_login(self.supervisor)
        response = http.get(reverse("supervisor:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "기수별 초기상담 기록지")
