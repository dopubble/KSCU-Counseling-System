"""상담사 확정 예약 직접 취소 테스트."""

from datetime import timedelta
from unittest.mock import patch

from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.cancellation_policy import AppointmentOperationError
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.counseling.services import (
    build_case_session_cards,
    cancel_confirmed_appointment_by_counselor,
)
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus


def _create_client(name: str = "내담자") -> User:
    return User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.CLIENT,
        status=UserStatus.ACTIVE,
    )


def _create_counselor(name: str = "상담사") -> User:
    user = User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.COUNSELOR,
        status=UserStatus.ACTIVE,
    )
    CounselorProfile.objects.get_or_create(user=user, defaults={"cohort": 1})
    return user


class CounselorDirectCancelTests(TestCase):
    def setUp(self):
        self.http = HttpClient()
        self.counselor = _create_counselor()
        self.client_user = _create_client()
        self.scheduled_at = timezone.now().replace(
            hour=15, minute=0, second=0, microsecond=0
        ) + timedelta(days=3)
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["진로상담"],
            reason="test",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.IN_PROGRESS,
        )
        self.case = Case.objects.create(
            application=application,
            client=self.client_user,
            counselor=self.counselor,
            case_number="CASE-CANCEL-1",
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.IN_PERSON,
            total_sessions=8,
            remaining_sessions=5,
        )
        self.appointment = Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            session_number=2,
            scheduled_at=self.scheduled_at,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.CONFIRMED,
            confirmed_at=timezone.now(),
        )

    def test_counselor_direct_cancel_sets_cancelled_without_session_penalty(self):
        self.case.remaining_sessions = 5
        self.case.save(update_fields=["remaining_sessions"])
        self.appointment.scheduled_at = timezone.now() + timedelta(hours=12)
        self.appointment.save(update_fields=["scheduled_at"])

        updated = cancel_confirmed_appointment_by_counselor(
            self.appointment,
            cancel_reason="상담사 일정으로 취소합니다.",
        )
        self.case.refresh_from_db()

        self.assertEqual(updated.status, AppointmentStatus.CANCELLED)
        self.assertEqual(updated.cancel_reason, "상담사 일정으로 취소합니다.")
        self.assertIsNotNone(updated.cancelled_at)
        self.assertEqual(self.case.remaining_sessions, 5)

    def test_counselor_direct_cancel_rejects_past_appointment(self):
        self.appointment.scheduled_at = timezone.now() - timedelta(hours=1)
        self.appointment.save(update_fields=["scheduled_at"])

        with self.assertRaises(AppointmentOperationError) as ctx:
            cancel_confirmed_appointment_by_counselor(
                self.appointment,
                cancel_reason="지난 예약 취소 시도",
            )
        self.assertEqual(ctx.exception.code, "past_appointment")

    @patch("apps.counseling.views.send_counselor_direct_cancel_notification", return_value=True)
    def test_counselor_cancel_view(self, _mock_email):
        self.http.force_login(self.counselor)
        url = reverse(
            "counselor:session_appointment_cancel",
            kwargs={"case_pk": self.case.pk, "appointment_pk": self.appointment.pk},
        )
        response = self.http.post(
            url,
            {"cancel_reason": "상담사 개인 사정으로 취소합니다."},
        )
        self.assertEqual(response.status_code, 302)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, AppointmentStatus.CANCELLED)

    def test_counselor_direct_cancel_shows_cancel_notice_not_rejection(self):
        cancel_confirmed_appointment_by_counselor(
            self.appointment,
            cancel_reason="상담사 일정으로 취소합니다.",
        )
        card = build_case_session_cards(self.case)[1]
        self.assertTrue(card.has_cancel_completed_notice)
        self.assertFalse(card.has_rejection_notice)
        self.assertEqual(card.client_status_label, "취소 완료")
