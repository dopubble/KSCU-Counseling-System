"""상담사 대시보드 — 종결 사례 열람 영역."""

from datetime import timedelta

from django.db import connection
from django.test import Client as HttpClient, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CaseTerminationType,
    CounselingApplication,
    CounselingMethod,
)
from apps.counseling.services import case_operation_label
from apps.scheduling.models import Appointment, AppointmentStatus
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
    total_sessions: int = 8,
    termination_type: str = "",
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
        termination_type=termination_type,
        counseling_method=CounselingMethod.IN_PERSON,
        total_sessions=total_sessions,
        remaining_sessions=remaining_sessions,
        closed_at=closed_at,
    )


def _progress_html(total, completed) -> str:
    return (
        f'총 <span class="fw-semibold">{total}</span>회기 / '
        f'<span class="fw-semibold">{completed}</span>회 완료'
    )


def _create_journal(case, session_number, *, is_draft=False):
    return CounselingJournal.objects.create(
        case=case,
        counselor=case.counselor,
        session_number=session_number,
        is_draft=is_draft,
        subjective="S",
        objective="O",
        assessment="A",
        plan="P",
    )


def _create_appointment(case, status, *, minutes=0):
    return Appointment.objects.create(
        case=case,
        counselor=case.counselor,
        client=case.client,
        scheduled_at=timezone.now() + timedelta(minutes=minutes),
        status=status,
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
        self.assertContains(response, "진행 현황")
        self.assertContains(response, _progress_html(8, 0))
        self.assertNotContains(response, ">진행중<")

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
        self.assertContains(response, "최종 제출 완료")
        self.assertNotContains(
            response,
            reverse(
                "counselor:case_records_submit", kwargs={"pk": self.closed_case.pk}
            ),
        )

    def test_active_and_closed_rows_show_submit_button(self):
        response = self.http.get(self.url)
        active_submit = reverse(
            "counselor:case_records_submit", kwargs={"pk": self.active_case.pk}
        )
        closed_submit = reverse(
            "counselor:case_records_submit", kwargs={"pk": self.closed_case.pk}
        )

        self.assertNotContains(response, active_submit)
        self.assertNotContains(response, closed_submit)
        self.assertNotContains(response, "최종 제출하기")
        self.assertNotContains(response, "사례기록 최종 제출")

    def test_other_counselor_does_not_see_submit_for_this_case(self):
        other_http = HttpClient()
        other_http.force_login(self.other_counselor)

        response = other_http.get(self.url)

        self.assertNotContains(response, "CASE-DASH-ACTIVE")
        self.assertNotContains(response, "CASE-DASH-CLOSED")
        self.assertNotContains(
            response,
            reverse(
                "counselor:case_records_submit", kwargs={"pk": self.active_case.pk}
            ),
        )


class CounselorDashboardProgressLabelTests(TestCase):
    def setUp(self):
        self.counselor = _create_counselor("현황상담사")
        self.http = HttpClient()
        self.http.force_login(self.counselor)
        self.url = reverse("counselor:dashboard")

    def _active_case(self, number, **kwargs):
        return _create_case(
            self.counselor,
            _create_client(number),
            number,
            total_sessions=kwargs.pop("total_sessions", 10),
            remaining_sessions=kwargs.pop("remaining_sessions", 7),
            **kwargs,
        )

    def test_finished_journals_count_completed_sessions(self):
        case = self._active_case("CASE-PROG-3")
        for session_number in range(1, 4):
            _create_journal(case, session_number)
        _create_journal(case, 4, is_draft=True)
        for index, status in enumerate(
            (
                AppointmentStatus.COMPLETED,
                AppointmentStatus.CONFIRMED,
                AppointmentStatus.PENDING,
                AppointmentStatus.CANCELLED,
                AppointmentStatus.NO_SHOW,
            )
        ):
            _create_appointment(case, status, minutes=index)

        response = self.http.get(self.url)

        self.assertContains(response, "진행 현황")
        self.assertContains(response, _progress_html(10, 3))
        self.assertEqual(response.context["cases"][0].completed_session_count, 3)
        self.assertNotContains(response, ">진행중<")

    def test_zero_completed_appointments(self):
        self._active_case("CASE-PROG-0")

        response = self.http.get(self.url)

        self.assertContains(response, _progress_html(10, 0))

    def test_finished_journal_counts_when_appointment_stays_confirmed(self):
        case = self._active_case("CASE-PROG-JOURNAL", remaining_sessions=6)
        _create_journal(case, 1)
        _create_appointment(case, AppointmentStatus.CONFIRMED, minutes=1)

        response = self.http.get(self.url)

        self.assertContains(response, _progress_html(10, 1))
        self.assertEqual(response.context["cases"][0].completed_session_count, 1)

    def test_completed_appointment_without_journal_counts_zero(self):
        case = self._active_case("CASE-PROG-APT")
        _create_appointment(case, AppointmentStatus.COMPLETED, minutes=1)

        response = self.http.get(self.url)

        self.assertContains(response, _progress_html(10, 0))
        self.assertEqual(response.context["cases"][0].completed_session_count, 0)

    def test_draft_journal_does_not_count(self):
        case = self._active_case("CASE-PROG-DRAFT")
        _create_journal(case, 1, is_draft=True)

        response = self.http.get(self.url)

        self.assertContains(response, _progress_html(10, 0))
        self.assertEqual(response.context["cases"][0].completed_session_count, 0)

    def test_remaining_sessions_drop_does_not_count_as_completed(self):
        self._active_case("CASE-PROG-REMAIN", remaining_sessions=4)

        response = self.http.get(self.url)

        self.assertContains(response, _progress_html(10, 0))

    def test_closure_ready_keeps_existing_label(self):
        ready = self._active_case("CASE-PROG-READY", remaining_sessions=0)
        _create_journal(ready, 1)
        _create_appointment(ready, AppointmentStatus.COMPLETED)

        response = self.http.get(self.url)

        self.assertContains(response, "종결준비")
        self.assertNotContains(response, _progress_html(10, 1))

    def test_normal_and_early_closure_keep_existing_labels(self):
        _create_case(
            self.counselor,
            _create_client("정상종결내담"),
            "CASE-PROG-NORMAL",
            status=CaseStatus.CLOSED,
            termination_type=CaseTerminationType.NORMAL,
            closed_at=timezone.now(),
            total_sessions=10,
            remaining_sessions=0,
        )
        _create_case(
            self.counselor,
            _create_client("조기종결내담"),
            "CASE-PROG-EARLY",
            status=CaseStatus.CLOSED,
            termination_type=CaseTerminationType.EARLY,
            closed_at=timezone.now(),
            total_sessions=10,
            remaining_sessions=4,
        )

        response = self.http.get(self.url)
        body = response.content.decode()

        self.assertContains(response, "상담 상태")
        self.assertIn("정상종결", body)
        self.assertIn("조기종결", body)
        self.assertNotIn(_progress_html(10, 0), body)

    def test_hold_and_transfer_labels_stay_unchanged_and_off_active_list(self):
        held = _create_case(
            self.counselor,
            _create_client("휴지내담"),
            "CASE-PROG-HOLD",
            status=CaseStatus.ON_HOLD,
            total_sessions=10,
            remaining_sessions=5,
        )
        transferred = _create_case(
            self.counselor,
            _create_client("이관내담"),
            "CASE-PROG-MOVE",
            status=CaseStatus.TRANSFERRED,
            total_sessions=10,
            remaining_sessions=5,
        )
        self.assertEqual(case_operation_label(held), "휴지")
        self.assertEqual(case_operation_label(transferred), "이관")

        response = self.http.get(self.url)

        active_numbers = [case.case_number for case in response.context["cases"]]
        self.assertNotIn("CASE-PROG-HOLD", active_numbers)
        self.assertNotIn("CASE-PROG-MOVE", active_numbers)
        self.assertNotContains(response, "CASE-PROG-HOLD")
        self.assertNotContains(response, "CASE-PROG-MOVE")

    def test_completed_journal_count_does_not_add_queries_per_case(self):
        first = self._active_case("CASE-PROG-Q1")
        _create_journal(first, 1)
        _create_appointment(first, AppointmentStatus.COMPLETED, minutes=1)
        _create_appointment(first, AppointmentStatus.CONFIRMED, minutes=2)
        with CaptureQueriesContext(connection) as before:
            self.http.get(self.url)

        for index in range(3):
            extra = self._active_case(f"CASE-PROG-Q{index + 2}")
            _create_journal(extra, 1)
            _create_journal(extra, 2, is_draft=True)
            _create_appointment(extra, AppointmentStatus.COMPLETED, minutes=10 + index)
            _create_appointment(extra, AppointmentStatus.CONFIRMED, minutes=20 + index)

        with CaptureQueriesContext(connection) as after:
            response = self.http.get(self.url)

        self.assertEqual(len(before.captured_queries), len(after.captured_queries))
        self.assertEqual(len(response.context["cases"]), 4)
        self.assertTrue(
            all(case.completed_session_count == 1 for case in response.context["cases"])
        )
