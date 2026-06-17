from datetime import datetime, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.counseling.services import assign_counselor
from apps.counseling.session1_bulk_import import (
    Session1MatchRow,
    repair_session1_confirmations_from_roster,
    verify_session1_roster,
)
from apps.reports.appointment_calendar import build_calendar_events, parse_calendar_bound
from apps.scheduling.models import Appointment, AppointmentStatus


def _create_client(name: str = "테스트내담자") -> User:
    return User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.CLIENT,
        status=UserStatus.ACTIVE,
    )


def _create_counselor(name: str = "테스트상담사") -> User:
    user = User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.COUNSELOR,
        status=UserStatus.ACTIVE,
    )
    CounselorProfile.objects.get_or_create(user=user, defaults={"cohort": 1})
    return user


class Session1RepairTests(TestCase):
    def setUp(self):
        self.client_user = _create_client("복구내담")
        self.counselor = _create_counselor("복구상담")
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["개인상담"],
            reason="테스트",
            status=ApplicationStatus.WAITING_MATCH,
        )
        self.case = assign_counselor(application, self.counselor, total_sessions=8)
        self.case.counseling_method = CounselingMethod.IN_PERSON
        self.case.status = CaseStatus.ACTIVE
        self.case.save(update_fields=["counseling_method", "status"])

        self.scheduled_at = timezone.make_aware(
            datetime(2026, 6, 17, 10, 0),
            timezone.get_current_timezone(),
        )
        self.row = Session1MatchRow(
            counselor_name=self.counselor.name,
            client_name=self.client_user.name,
            first_session=self.scheduled_at,
            counseling_method=CounselingMethod.IN_PERSON,
        )

    def test_verify_flags_pending_session1(self):
        Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            duration_minutes=50,
            status=AppointmentStatus.PENDING,
            session_number=1,
        )
        report = verify_session1_roster([self.row])
        kinds = {issue.kind for issue in report.issues}
        self.assertIn("session1_not_confirmed", kinds)

    def test_repair_confirms_pending_in_person_session1(self):
        Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            duration_minutes=50,
            status=AppointmentStatus.PENDING,
            session_number=1,
        )
        results = repair_session1_confirmations_from_roster(
            [self.row],
            dry_run=False,
        )
        self.assertEqual(results[0].status, "confirmed")
        apt = Appointment.objects.get(case=self.case, session_number=1)
        self.assertEqual(apt.status, AppointmentStatus.CONFIRMED)

        day_start = parse_calendar_bound("2026-06-17T00:00:00+09:00")
        day_end = parse_calendar_bound("2026-06-18T00:00:00+09:00")
        events = build_calendar_events(start=day_start, end=day_end)
        names = [event["extendedProps"]["client_name"] for event in events]
        self.assertIn(self.client_user.name, names)

    def test_repair_dry_run_reports_pending_without_db_change(self):
        Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            duration_minutes=50,
            status=AppointmentStatus.PENDING,
            session_number=1,
        )
        results = repair_session1_confirmations_from_roster([self.row], dry_run=True)
        self.assertEqual(results[0].status, "confirm")
        apt = Appointment.objects.get(case=self.case, session_number=1)
        self.assertEqual(apt.status, AppointmentStatus.PENDING)
