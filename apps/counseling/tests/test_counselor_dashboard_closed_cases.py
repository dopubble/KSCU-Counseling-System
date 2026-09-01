"""상담사 대시보드 — 종결 사례 열람 영역."""

from datetime import timedelta

from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.sessions_app.models import CounselingJournal, TerminationCounselingRecord


def _create_client(name: str) -> User:
    return User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.CLIENT,
        status=UserStatus.ACTIVE,
    )


def _create_counselor(name: str) -> User:
    user = User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.COUNSELOR,
        status=UserStatus.ACTIVE,
    )
    CounselorProfile.objects.update_or_create(
        user=user,
        defaults={"cohort": 1, "is_approved": True},
    )
    return user


def _create_case(
    counselor: User,
    client: User,
    case_number: str,
    *,
    status: str = CaseStatus.ACTIVE,
    closed_at=None,
    remaining_sessions: int = 8,
) -> Case:
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
        total_sessions=8,
        remaining_sessions=remaining_sessions,
        closed_at=closed_at,
    )


class CounselorDashboardClosedCaseTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.counselor = _create_counselor("상담사갑")
        self.other_counselor = _create_counselor("상담사을")

        self.active_case = _create_case(
            self.counselor,
            _create_client("진행내담자"),
            "CASE-DASH-ACTIVE",
        )
        self.closed_case = _create_case(
            self.counselor,
            _create_client("종결내담자"),
            "CASE-DASH-CLOSED",
            status=CaseStatus.CLOSED,
            closed_at=self.now - timedelta(days=1),
            remaining_sessions=0,
        )
        self.other_closed_case = _create_case(
            self.other_counselor,
            _create_client("타상담사내담자"),
            "CASE-DASH-OTHER",
            status=CaseStatus.CLOSED,
            closed_at=self.now,
            remaining_sessions=0,
        )

        self.http = HttpClient()
        self.http.force_login(self.counselor)
        self.url = reverse("counselor:dashboard")

    def test_active_case_still_listed_in_active_section(self):
        response = self.http.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [case.pk for case in response.context["cases"]],
            [self.active_case.pk],
        )
        self.assertEqual(response.context["active_count"], 1)
        self.assertContains(response, "CASE-DASH-ACTIVE")
        self.assertContains(response, "개입중")

    def test_closed_case_listed_in_closed_section(self):
        response = self.http.get(self.url)

        self.assertEqual(
            [case.pk for case in response.context["closed_cases"]],
            [self.closed_case.pk],
        )
        self.assertEqual(response.context["closed_count"], 1)
        self.assertContains(response, "종결 사례")
        self.assertContains(response, "badge-counselor-closed")
        self.assertContains(response, "CASE-DASH-CLOSED")
        self.assertEqual(
            response.context["closed_cases"][0].get_status_display(), "종결"
        )

    def test_closed_case_excluded_from_active_context(self):
        response = self.http.get(self.url)

        active_pks = [case.pk for case in response.context["cases"]]
        self.assertNotIn(self.closed_case.pk, active_pks)

    def test_other_counselor_closed_case_not_listed(self):
        response = self.http.get(self.url)

        closed_pks = [case.pk for case in response.context["closed_cases"]]
        self.assertNotIn(self.other_closed_case.pk, closed_pks)
        self.assertNotContains(response, "CASE-DASH-OTHER")
        self.assertNotContains(response, "타상담사내담자")

    def test_closed_section_visible_only_to_assigned_counselor(self):
        other_http = HttpClient()
        other_http.force_login(self.other_counselor)

        response = other_http.get(self.url)

        self.assertEqual(
            [case.pk for case in response.context["closed_cases"]],
            [self.other_closed_case.pk],
        )
        self.assertNotContains(response, "CASE-DASH-CLOSED")
        self.assertContains(response, "현재 배정된 활성 사례가 없습니다.")

    def test_closed_cases_ordered_by_closed_at_desc(self):
        newer = _create_case(
            self.counselor,
            _create_client("최근종결내담자"),
            "CASE-DASH-CLOSED-2",
            status=CaseStatus.CLOSED,
            closed_at=self.now,
            remaining_sessions=0,
        )
        manual = _create_case(
            self.counselor,
            _create_client("수동종결내담자"),
            "CASE-DASH-CLOSED-3",
            status=CaseStatus.CLOSED,
            closed_at=None,
            remaining_sessions=0,
        )

        response = self.http.get(self.url)

        self.assertEqual(
            [case.pk for case in response.context["closed_cases"]],
            [newer.pk, self.closed_case.pk, manual.pk],
        )

    def test_closed_case_detail_and_journal_remain_accessible(self):
        journal = CounselingJournal.objects.create(
            case=self.closed_case,
            counselor=self.counselor,
            session_number=8,
            is_draft=False,
            subjective="S",
            objective="O",
            assessment="A",
            plan="P",
        )
        TerminationCounselingRecord.objects.create(
            case=self.closed_case,
            counselor=self.counselor,
            is_draft=False,
            termination_reason="회기 종료",
        )

        detail = self.http.get(
            reverse("counselor:case_detail", kwargs={"pk": self.closed_case.pk})
        )
        self.assertEqual(detail.status_code, 200)

        journal_detail = self.http.get(
            reverse(
                "counselor:journal_detail",
                kwargs={"pk": self.closed_case.pk, "session_number": journal.session_number},
            )
        )
        self.assertEqual(journal_detail.status_code, 200)

        termination_detail = self.http.get(
            reverse(
                "counselor:termination_record_detail",
                kwargs={"pk": self.closed_case.pk},
            )
        )
        self.assertEqual(termination_detail.status_code, 200)

    def test_other_counselor_cannot_open_closed_case_detail(self):
        other_http = HttpClient()
        other_http.force_login(self.other_counselor)

        response = other_http.get(
            reverse("counselor:case_detail", kwargs={"pk": self.closed_case.pk})
        )

        self.assertEqual(response.status_code, 404)

    def test_dashboard_does_not_change_case_state(self):
        self.http.get(self.url)

        self.closed_case.refresh_from_db()
        self.active_case.refresh_from_db()
        self.assertEqual(self.closed_case.status, CaseStatus.CLOSED)
        self.assertEqual(self.closed_case.remaining_sessions, 0)
        self.assertEqual(self.active_case.status, CaseStatus.ACTIVE)
        self.assertEqual(self.active_case.remaining_sessions, 8)

    def test_submitted_closed_case_remains_in_closed_section(self):
        self.closed_case.records_submitted_at = timezone.now()
        self.closed_case.records_submitted_by = self.counselor
        self.closed_case.save(
            update_fields=["records_submitted_at", "records_submitted_by"]
        )

        response = self.http.get(self.url)

        self.assertContains(response, "CASE-DASH-CLOSED")
        self.assertIn(
            self.closed_case.pk,
            [case.pk for case in response.context["closed_cases"]],
        )
