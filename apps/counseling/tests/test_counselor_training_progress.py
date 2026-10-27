"""상담사 대시보드 수련 현황 — 상담일지 작성 집계."""

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
from apps.counseling.training_progress import get_counselor_training_progress
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.sessions_app.models import CounselingJournal


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


def _create_case(counselor, client, case_number, *, status=CaseStatus.ACTIVE):
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
        closed_at=timezone.now() if status == CaseStatus.CLOSED else None,
    )


def _create_appointment(case, *, status, session_number, when):
    return Appointment.objects.create(
        case=case,
        counselor=case.counselor,
        client=case.client,
        scheduled_at=when,
        status=status,
        session_number=session_number,
    )


def _create_journal(case, session_number, *, is_draft=False, appointment=None):
    return CounselingJournal.objects.create(
        case=case,
        counselor=case.counselor,
        session_number=session_number,
        is_draft=is_draft,
        appointment=appointment,
        subjective="S",
        objective="O",
        assessment="A",
        plan="P",
    )


class CounselorTrainingProgressTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.counselor = _create_counselor("수련상담사")
        self.other = _create_counselor("다른상담사")
        self.case = _create_case(self.counselor, _create_client("수련내담"), "CASE-TRAIN-1")
        self.other_case = _create_case(
            self.other, _create_client("다른내담"), "CASE-TRAIN-OTHER"
        )

    def test_empty_counselor_is_all_zero(self):
        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(
            progress,
            {
                "journal_completed_count": 0,
                "completed_appointment_count": 0,
                "journal_missing_count": 0,
                "journal_draft_count": 0,
            },
        )

    def test_other_counselor_records_are_excluded(self):
        appointment = _create_appointment(
            self.other_case,
            status=AppointmentStatus.COMPLETED,
            session_number=1,
            when=self.now - timedelta(days=3),
        )
        _create_journal(self.other_case, 1, appointment=appointment)
        _create_journal(self.other_case, 2, is_draft=True)

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["journal_completed_count"], 0)
        self.assertEqual(progress["completed_appointment_count"], 0)
        self.assertEqual(progress["journal_missing_count"], 0)
        self.assertEqual(progress["journal_draft_count"], 0)

    def test_only_completed_appointments_count_as_finished_sessions(self):
        statuses = [
            AppointmentStatus.PENDING,
            AppointmentStatus.SCHEDULED,
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.CANCEL_PENDING,
            AppointmentStatus.CANCELLED,
            AppointmentStatus.NO_SHOW,
            AppointmentStatus.COMPLETED,
        ]
        for index, status in enumerate(statuses, start=1):
            _create_appointment(
                self.case,
                status=status,
                session_number=index,
                when=self.now + timedelta(days=index),
            )

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["completed_appointment_count"], 1)
        self.assertEqual(progress["journal_missing_count"], 1)
        self.assertEqual(progress["journal_completed_count"], 0)

    def test_confirmed_future_appointment_is_not_missing(self):
        _create_appointment(
            self.case,
            status=AppointmentStatus.CONFIRMED,
            session_number=1,
            when=self.now + timedelta(days=14),
        )

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["completed_appointment_count"], 0)
        self.assertEqual(progress["journal_missing_count"], 0)

    def test_completed_journal_and_draft_are_counted_separately(self):
        completed = _create_appointment(
            self.case,
            status=AppointmentStatus.COMPLETED,
            session_number=1,
            when=self.now - timedelta(days=2),
        )
        draft_appointment = _create_appointment(
            self.case,
            status=AppointmentStatus.COMPLETED,
            session_number=2,
            when=self.now - timedelta(days=1),
        )
        _create_journal(self.case, 1, is_draft=False, appointment=completed)
        _create_journal(self.case, 2, is_draft=True, appointment=draft_appointment)

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["journal_completed_count"], 1)
        self.assertEqual(progress["journal_draft_count"], 1)
        self.assertEqual(progress["completed_appointment_count"], 2)
        self.assertEqual(progress["journal_missing_count"], 1)

    def test_completed_appointment_with_finished_journal_is_not_missing(self):
        appointment = _create_appointment(
            self.case,
            status=AppointmentStatus.COMPLETED,
            session_number=1,
            when=self.now - timedelta(days=1),
        )
        _create_journal(self.case, 1, is_draft=False, appointment=appointment)

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["journal_missing_count"], 0)
        self.assertEqual(progress["completed_appointment_count"], 1)
        self.assertEqual(progress["journal_completed_count"], 1)

    def test_journal_without_appointment_does_not_change_completed_sessions(self):
        missing = _create_appointment(
            self.case,
            status=AppointmentStatus.COMPLETED,
            session_number=1,
            when=self.now - timedelta(days=2),
        )
        _create_journal(self.case, 2, is_draft=False, appointment=None)

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["journal_completed_count"], 1)
        self.assertEqual(progress["completed_appointment_count"], 1)
        self.assertEqual(progress["journal_missing_count"], 1)
        self.assertIsNone(missing.journals.filter(is_draft=False).first())

    def test_closed_case_journals_count_for_the_counselor(self):
        closed = _create_case(
            self.counselor,
            _create_client("종결내담"),
            "CASE-TRAIN-CLOSED",
            status=CaseStatus.CLOSED,
        )
        appointment = _create_appointment(
            closed,
            status=AppointmentStatus.COMPLETED,
            session_number=1,
            when=self.now - timedelta(days=30),
        )
        _create_journal(closed, 1, is_draft=False, appointment=appointment)

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["journal_completed_count"], 1)
        self.assertEqual(progress["completed_appointment_count"], 1)
        self.assertEqual(progress["journal_missing_count"], 0)

    def test_query_count_does_not_grow_with_more_cases(self):
        _create_appointment(
            self.case,
            status=AppointmentStatus.COMPLETED,
            session_number=1,
            when=self.now - timedelta(days=1),
        )
        with self.assertNumQueries(1):
            get_counselor_training_progress(self.counselor)

        for index in range(3):
            extra = _create_case(
                self.counselor,
                _create_client(f"추가내담{index}"),
                f"CASE-TRAIN-Q{index}",
            )
            appointment = _create_appointment(
                extra,
                status=AppointmentStatus.COMPLETED,
                session_number=1,
                when=self.now - timedelta(days=10 + index),
            )
            _create_journal(extra, 1, appointment=appointment)

        with self.assertNumQueries(1):
            get_counselor_training_progress(self.counselor)


class CounselorTrainingProgressDashboardTests(TestCase):
    def setUp(self):
        self.counselor = _create_counselor("화면상담사")
        self.http = HttpClient()
        self.http.force_login(self.counselor)
        self.url = reverse("counselor:dashboard")

    def test_dashboard_renders_zero_training_progress(self):
        response = self.http.get(self.url)

        self.assertEqual(response.status_code, 200)
        progress = response.context["training_progress"]
        self.assertEqual(progress["journal_completed_count"], 0)
        self.assertEqual(progress["completed_appointment_count"], 0)
        self.assertEqual(progress["journal_missing_count"], 0)
        self.assertEqual(progress["journal_draft_count"], 0)
        self.assertContains(response, "수련 현황")
        self.assertContains(response, "상담일지")
        self.assertContains(response, "작성완료 0 / 완료 회기 0")
        self.assertContains(response, "미작성 0회")
        self.assertContains(response, "작성중 0회")
        self.assertContains(response, "사례발표 게시판")
        self.assertNotContains(response, "출석률")
        self.assertIn("cases", response.context)
        self.assertIn("active_count", response.context)
        self.assertIn("closed_cases", response.context)
        self.assertIn("pending_appointments", response.context)

    def test_dashboard_renders_calculated_counts(self):
        case = _create_case(self.counselor, _create_client("화면내담"), "CASE-TRAIN-UI")
        now = timezone.now()
        finished = _create_appointment(
            case,
            status=AppointmentStatus.COMPLETED,
            session_number=1,
            when=now - timedelta(days=2),
        )
        _create_appointment(
            case,
            status=AppointmentStatus.COMPLETED,
            session_number=2,
            when=now - timedelta(days=1),
        )
        _create_journal(case, 1, is_draft=False, appointment=finished)
        _create_journal(case, 3, is_draft=True)

        response = self.http.get(self.url)

        self.assertContains(response, "작성완료 1 / 완료 회기 2")
        self.assertContains(response, "미작성 1회")
        self.assertContains(response, "작성중 1회")

    def test_dashboard_text_matches_progress_for_mixed_records(self):
        now = timezone.now()
        active = _create_case(
            self.counselor, _create_client("진행내담"), "CASE-TRAIN-MIX-A"
        )
        closed = _create_case(
            self.counselor,
            _create_client("종결내담"),
            "CASE-TRAIN-MIX-C",
            status=CaseStatus.CLOSED,
        )
        linked = _create_appointment(
            closed,
            status=AppointmentStatus.COMPLETED,
            session_number=1,
            when=now - timedelta(days=20),
        )
        _create_journal(closed, 1, is_draft=False, appointment=linked)
        _create_appointment(
            active,
            status=AppointmentStatus.COMPLETED,
            session_number=1,
            when=now - timedelta(days=2),
        )
        _create_journal(active, 2, is_draft=False, appointment=None)
        _create_journal(active, 3, is_draft=True)
        _create_appointment(
            active,
            status=AppointmentStatus.CONFIRMED,
            session_number=4,
            when=now + timedelta(days=7),
        )

        progress = get_counselor_training_progress(self.counselor)
        response = self.http.get(self.url)
        body = response.content.decode()

        self.assertEqual(progress["journal_completed_count"], 2)
        self.assertEqual(progress["completed_appointment_count"], 2)
        self.assertEqual(progress["journal_missing_count"], 1)
        self.assertEqual(progress["journal_draft_count"], 1)
        self.assertEqual(response.context["training_progress"], progress)
        self.assertIn(
            f"작성완료 {progress['journal_completed_count']} / 완료 회기 {progress['completed_appointment_count']}",
            body,
        )
        self.assertIn(f"미작성 {progress['journal_missing_count']}회", body)
        self.assertIn(f"작성중 {progress['journal_draft_count']}회", body)
