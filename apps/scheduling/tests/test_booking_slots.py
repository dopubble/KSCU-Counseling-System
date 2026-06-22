"""예약 캘린더 슬롯 API·상태 계산 테스트."""

from datetime import datetime, time, timedelta

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
from apps.scheduling.availability import local_timezone
from apps.scheduling.booking_slots import build_booking_slots_for_date, resolve_slot_state
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus


class BookingSlotsTests(TestCase):
    def setUp(self):
        self.http = HttpClient()
        self.on_date = (timezone.now() + timedelta(days=10)).date()
        tz = local_timezone()
        self.slot_start = timezone.make_aware(
            datetime.combine(self.on_date, time(14, 0)),
            tz,
        )

        self.client_user = User.objects.create_user(
            email="client@example.com",
            password="pass12345",
            name="내담자",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.counselor = User.objects.create_user(
            email="counselor@example.com",
            password="pass12345",
            name="상담사",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.get_or_create(user=self.counselor, defaults={"cohort": 1})

    def _create_case(self, method: str) -> Case:
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["진로상담"],
            reason="test",
            counseling_method=method,
            status=ApplicationStatus.IN_PROGRESS,
        )
        return Case.objects.create(
            application=application,
            client=self.client_user,
            counselor=self.counselor,
            case_number=f"CASE-{method}",
            status=CaseStatus.ACTIVE,
            counseling_method=method,
        )

    def test_booking_slots_api_returns_room_full_for_in_person(self):
        case = self._create_case(CounselingMethod.IN_PERSON)
        counselor_b = User.objects.create_user(
            email="counselor2@example.com",
            password="pass12345",
            name="상담사2",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.get_or_create(user=counselor_b, defaults={"cohort": 1})
        other_client = User.objects.create_user(
            email="other@example.com",
            password="pass12345",
            name="다른내담자",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        for idx in range(2):
            other_app = CounselingApplication.objects.create(
                client=other_client,
                counseling_types=["진로상담"],
                reason=f"o{idx}",
                counseling_method=CounselingMethod.IN_PERSON,
                status=ApplicationStatus.IN_PROGRESS,
            )
            other_case = Case.objects.create(
                application=other_app,
                client=other_client,
                counselor=counselor_b,
                case_number=f"CASE-O-{idx}",
                status=CaseStatus.ACTIVE,
                counseling_method=CounselingMethod.IN_PERSON,
            )
            Appointment.objects.create(
                case=other_case,
                counselor=counselor_b,
                client=other_client,
                scheduled_at=self.slot_start + timedelta(minutes=idx * 30),
                duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
                status=AppointmentStatus.CONFIRMED,
                confirmed_at=timezone.now(),
            )

        self.http.force_login(self.client_user)
        url = reverse("scheduling:booking_slots")
        response = self.http.get(
            url,
            {"case_id": str(case.pk), "date": self.on_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        room_full_slots = [slot for slot in payload["slots"] if slot["state"] == "room_full"]
        self.assertTrue(room_full_slots, payload["slots"])

    def test_resolve_slot_state_marks_counselor_overlap_as_taken(self):
        case = self._create_case(CounselingMethod.IN_PERSON)
        Appointment.objects.create(
            case=case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.slot_start,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.CONFIRMED,
            confirmed_at=timezone.now(),
        )
        state = resolve_slot_state(
            counselor_id=self.counselor.pk,
            counseling_method=CounselingMethod.IN_PERSON,
            scheduled_at=self.slot_start + timedelta(hours=1),
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
        )
        self.assertEqual(state, "available")

        state_same = resolve_slot_state(
            counselor_id=self.counselor.pk,
            counseling_method=CounselingMethod.IN_PERSON,
            scheduled_at=self.slot_start,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
        )
        self.assertEqual(state_same, "taken")

    def test_build_booking_slots_for_date_includes_nine_am_slot(self):
        case = self._create_case(CounselingMethod.REMOTE)
        slots = build_booking_slots_for_date(case=case, on_date=self.on_date)
        labels = [slot.label for slot in slots]
        self.assertTrue(any(label.startswith("09:00") for label in labels))
