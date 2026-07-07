"""80분 버퍼·최대 2건 비대면 용량 정책 테스트."""

from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.remote_zoom_capacity import (
    REMOTE_ZOOM_CAPACITY_FULL_MESSAGE,
    is_remote_zoom_slot_available,
    remote_zoom_buffer_overlapping_remaining,
)
from apps.scheduling.services import AppointmentServiceError, confirm_appointment_with_zoom
from apps.scheduling.validators import validate_remote_zoom_concurrency
from apps.scheduling.zoom_capacity import (
    count_buffer_overlapping_confirmed_remote,
    is_remote_zoom_buffer_slot_available,
    remote_zoom_licensed_slot_limit,
)


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


def _create_remote_case(client: User, counselor: User, label: str) -> Case:
    application = CounselingApplication.objects.create(
        client=client,
        counseling_types=["진로상담"],
        reason=label,
        counseling_method=CounselingMethod.REMOTE,
        status=ApplicationStatus.IN_PROGRESS,
    )
    return Case.objects.create(
        application=application,
        client=client,
        counselor=counselor,
        case_number=f"CASE-{label}",
        status=CaseStatus.ACTIVE,
        counseling_method=CounselingMethod.REMOTE,
    )


def _create_confirmed_remote(
    case: Case,
    *,
    scheduled_at,
    counselor: User | None = None,
) -> Appointment:
    apt = Appointment.objects.create(
        case=case,
        counselor=counselor or case.counselor,
        client=case.client,
        scheduled_at=scheduled_at,
        duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
        status=AppointmentStatus.CONFIRMED,
        confirmed_at=timezone.now(),
    )
    return apt


@override_settings(
    ZOOM_LICENSED_USERS="host1@example.com,host2@example.com",
    ZOOM_HOST_BUFFER_MINUTES=30,
)
class RemoteZoomCapacityPolicyTests(TestCase):
    def setUp(self):
        self.base = timezone.now().replace(
            hour=10, minute=0, second=0, microsecond=0
        ) + timedelta(days=14)
        self.counselor_a = _create_counselor("상담사A")
        self.counselor_b = _create_counselor("상담사B")

    def test_licensed_slot_limit_is_two(self):
        self.assertEqual(remote_zoom_licensed_slot_limit(), 2)

    def test_staggered_overlap_counts_two_within_buffer_window(self):
        client1 = _create_client("내담자1")
        client2 = _create_client("내담자2")
        case1 = _create_remote_case(client1, self.counselor_a, "A")
        case2 = _create_remote_case(client2, self.counselor_b, "B")
        _create_confirmed_remote(case1, scheduled_at=self.base)
        _create_confirmed_remote(case2, scheduled_at=self.base + timedelta(minutes=30))

        count = count_buffer_overlapping_confirmed_remote(
            scheduled_at=self.base + timedelta(minutes=30),
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
        )
        self.assertEqual(count, 2)

    def test_third_slot_in_buffer_window_blocked(self):
        client1 = _create_client("내담자1")
        client2 = _create_client("내담자2")
        case1 = _create_remote_case(client1, self.counselor_a, "A")
        case2 = _create_remote_case(client2, self.counselor_b, "B")
        _create_confirmed_remote(case1, scheduled_at=self.base)
        _create_confirmed_remote(case2, scheduled_at=self.base + timedelta(minutes=30))

        self.assertFalse(
            is_remote_zoom_buffer_slot_available(
                scheduled_at=self.base + timedelta(hours=1),
                duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            )
        )
        self.assertEqual(
            remote_zoom_buffer_overlapping_remaining(
                scheduled_at=self.base + timedelta(hours=1),
                duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            ),
            0,
        )

    def test_non_overlapping_after_buffer_allowed(self):
        client1 = _create_client("내담자1")
        case1 = _create_remote_case(client1, self.counselor_a, "A")
        _create_confirmed_remote(case1, scheduled_at=self.base)

        # 10:00 + 50분 + 30분 버퍼 = 11:20 종료 → 11:30부터 여유
        self.assertTrue(
            is_remote_zoom_buffer_slot_available(
                scheduled_at=self.base + timedelta(hours=1, minutes=30),
                duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            )
        )

    def test_confirm_rejects_third_overlapping_within_buffer_window(self):
        client1 = _create_client("내담자1")
        client2 = _create_client("내담자2")
        client3 = _create_client("내담자3")
        counselor_c = _create_counselor("상담사C")
        case1 = _create_remote_case(client1, self.counselor_a, "A")
        case2 = _create_remote_case(client2, self.counselor_b, "B")
        case3 = _create_remote_case(client3, counselor_c, "C")
        _create_confirmed_remote(case1, scheduled_at=self.base)
        _create_confirmed_remote(case2, scheduled_at=self.base + timedelta(minutes=30))

        pending = Appointment.objects.create(
            case=case3,
            counselor=case3.counselor,
            client=case3.client,
            scheduled_at=self.base + timedelta(hours=1),
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.PENDING,
        )

        with patch("apps.scheduling.services._create_zoom_meeting_for_appointment"):
            with self.assertRaises(AppointmentServiceError) as ctx:
                confirm_appointment_with_zoom(pending, notify=False)
            self.assertEqual(str(ctx.exception), REMOTE_ZOOM_CAPACITY_FULL_MESSAGE)

    def test_appointment_clean_rejects_over_capacity(self):
        client1 = _create_client("내담자1")
        client2 = _create_client("내담자2")
        case1 = _create_remote_case(client1, self.counselor_a, "A")
        case2 = _create_remote_case(client2, self.counselor_b, "B")
        _create_confirmed_remote(case1, scheduled_at=self.base)
        _create_confirmed_remote(case2, scheduled_at=self.base + timedelta(minutes=30))

        client3 = _create_client("내담자3")
        case3 = _create_remote_case(client3, self.counselor_a, "C")
        overflow = Appointment(
            case=case3,
            counselor=case3.counselor,
            client=case3.client,
            scheduled_at=self.base + timedelta(hours=1),
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.CONFIRMED,
            confirmed_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            validate_remote_zoom_concurrency(overflow)

    def test_is_remote_zoom_slot_available_requires_host_after_capacity(self):
        client1 = _create_client("내담자1")
        client2 = _create_client("내담자2")
        case1 = _create_remote_case(client1, self.counselor_a, "A")
        case2 = _create_remote_case(client2, self.counselor_b, "B")
        _create_confirmed_remote(case1, scheduled_at=self.base)
        _create_confirmed_remote(case2, scheduled_at=self.base + timedelta(minutes=30))

        self.assertFalse(
            is_remote_zoom_slot_available(
                scheduled_at=self.base + timedelta(hours=1),
                duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            )
        )

    def test_booking_slot_10am_zoom_full_and_zero_remaining_when_11am_has_two(self):
        """11:00 확정 2건 → 10:00 슬롯 zoom_full, 비대면 여석 0 (상담사 UI)."""
        from datetime import datetime, time as time_cls

        from apps.scheduling.availability import local_timezone
        from apps.scheduling.booking_slots import build_booking_slots_for_date
        from apps.scheduling.models import CounselorAvailability

        tz = local_timezone()
        on_date = self.base.date()
        eleven = timezone.make_aware(datetime.combine(on_date, time_cls(11, 0)), tz)

        CounselorAvailability.objects.create(
            counselor=self.counselor_a,
            is_recurring=True,
            day_of_week=on_date.weekday(),
            start_time=time_cls(9, 0),
            end_time=time_cls(22, 0),
            is_available=True,
        )

        client1 = _create_client("내담자1")
        client2 = _create_client("내담자2")
        case1 = _create_remote_case(client1, self.counselor_a, "A")
        case2 = _create_remote_case(client2, self.counselor_b, "B")
        _create_confirmed_remote(case1, scheduled_at=eleven)
        _create_confirmed_remote(case2, scheduled_at=eleven)

        booker = _create_client("예약시도")
        counselor_c = _create_counselor("상담사C")
        CounselorAvailability.objects.create(
            counselor=counselor_c,
            is_recurring=True,
            day_of_week=on_date.weekday(),
            start_time=time_cls(9, 0),
            end_time=time_cls(22, 0),
            is_available=True,
        )
        booker_case = _create_remote_case(booker, counselor_c, "BOOK")
        slots = build_booking_slots_for_date(
            case=booker_case,
            on_date=on_date,
            include_venue_remainings=True,
        )
        slot_10 = next(s for s in slots if s.start.hour == 10 and s.start.minute == 0)
        slot_11 = next(s for s in slots if s.start.hour == 11 and s.start.minute == 0)

        self.assertEqual(slot_10.state, "zoom_full")
        self.assertEqual(slot_10.zoom_remaining, 0)
        self.assertEqual(slot_11.state, "zoom_full")
        self.assertEqual(slot_11.zoom_remaining, 0)
