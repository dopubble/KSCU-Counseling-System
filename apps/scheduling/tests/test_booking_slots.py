"""예약 캘린더 슬롯 API·상태 계산 테스트."""

from datetime import date, datetime, time, timedelta

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
from apps.scheduling.availability import is_counselor_slot_available, local_timezone
from apps.scheduling.booking_slots import (
    build_available_dates_for_month,
    build_booking_slots_for_date,
    month_date_bounds,
    resolve_slot_state,
)
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus, CounselorAvailability


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

    def _add_weekday_availability(self, start: time, end: time) -> None:
        for day in range(5):
            CounselorAvailability.objects.create(
                counselor=self.counselor,
                is_recurring=True,
                day_of_week=day,
                start_time=start,
                end_time=end,
                is_available=True,
                is_active=True,
            )

    def _next_weekday(self, weekday: int) -> date:
        cursor = timezone.localdate() + timedelta(days=1)
        while cursor.weekday() != weekday:
            cursor += timedelta(days=1)
        return cursor

    def test_weekend_blocked_when_only_weekday_recurring_rules(self):
        self._add_weekday_availability(time(14, 0), time(22, 41))
        case = self._create_case(CounselingMethod.REMOTE)
        saturday = self._next_weekday(5)
        tz = local_timezone()
        slot_start = timezone.make_aware(datetime.combine(saturday, time(15, 0)), tz)

        available, message = is_counselor_slot_available(
            self.counselor.pk,
            slot_start,
            require_full_duration=True,
        )
        self.assertFalse(available)
        self.assertIn("요일", message)

        slots = build_booking_slots_for_date(case=case, on_date=saturday)
        self.assertFalse(any(slot.state == "available" for slot in slots))

    def test_weekday_allowed_within_recurring_window(self):
        self._add_weekday_availability(time(14, 0), time(22, 41))
        case = self._create_case(CounselingMethod.REMOTE)
        monday = self._next_weekday(0)
        tz = local_timezone()
        slot_start = timezone.make_aware(datetime.combine(monday, time(15, 0)), tz)

        available, _message = is_counselor_slot_available(
            self.counselor.pk,
            slot_start,
            require_full_duration=True,
        )
        self.assertTrue(available)

        slots = build_booking_slots_for_date(
            case=case,
            on_date=monday,
            require_full_duration=True,
        )
        available_slots = [slot for slot in slots if slot.state == "available"]
        self.assertTrue(available_slots)
        self.assertTrue(all(slot.start.hour >= 14 for slot in available_slots))

    def test_available_dates_for_month_excludes_weekends(self):
        self._add_weekday_availability(time(14, 0), time(22, 41))
        case = self._create_case(CounselingMethod.REMOTE)
        anchor = self._next_weekday(0)
        month_start, month_end = month_date_bounds(anchor.year, anchor.month)
        dates = build_available_dates_for_month(
            case=case,
            month_start=month_start,
            month_end=month_end,
            require_full_duration=True,
        )
        for date_text in dates:
            parsed = date.fromisoformat(date_text)
            self.assertLess(parsed.weekday(), 5, date_text)

    def test_counselor_booking_slots_include_dual_venue_remaining(self):
        self._add_weekday_availability(time(14, 0), time(22, 41))
        case = self._create_case(CounselingMethod.REMOTE)
        monday = self._next_weekday(0)
        tz = local_timezone()
        slot_start = timezone.make_aware(datetime.combine(monday, time(15, 0)), tz)

        other_client = User.objects.create_user(
            email="other@example.com",
            password="pass12345",
            name="다른내담자",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        other_app = CounselingApplication.objects.create(
            client=other_client,
            counseling_types=["진로상담"],
            reason="remote",
            counseling_method=CounselingMethod.REMOTE,
            status=ApplicationStatus.IN_PROGRESS,
        )
        other_case = Case.objects.create(
            application=other_app,
            client=other_client,
            counselor=self.counselor,
            case_number="CASE-REMOTE-OTHER",
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.REMOTE,
        )
        Appointment.objects.create(
            case=other_case,
            counselor=self.counselor,
            client=other_client,
            scheduled_at=slot_start,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.CONFIRMED,
            confirmed_at=timezone.now(),
        )

        self.http.force_login(self.counselor)
        url = reverse("scheduling:booking_slots")
        response = self.http.get(
            url,
            {"case_id": str(case.pk), "date": monday.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        slot_15 = next(
            slot for slot in payload["slots"] if slot["label"].startswith("15:00")
        )
        self.assertIn("room_remaining", slot_15)
        self.assertIn("zoom_remaining", slot_15)
        self.assertEqual(slot_15["room_remaining"], 2)
        self.assertEqual(slot_15["zoom_remaining"], 1)
