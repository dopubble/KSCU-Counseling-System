"""대면 상담실 동시 예약 용량 검증 테스트."""

from datetime import timedelta

from django.test import TestCase
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
from apps.scheduling.in_person_room_capacity import (
    IN_PERSON_ROOM_CAPACITY_FULL_MESSAGE,
    check_in_person_room_capacity,
    count_overlapping_confirmed_in_person,
)
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.services import (
    AppointmentServiceError,
    confirm_appointment_with_zoom,
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


def _create_in_person_case(client: User, counselor: User, label: str) -> Case:
    application = CounselingApplication.objects.create(
        client=client,
        counseling_types=["진로상담"],
        reason=label,
        counseling_method=CounselingMethod.IN_PERSON,
        status=ApplicationStatus.IN_PROGRESS,
    )
    return Case.objects.create(
        application=application,
        client=client,
        counselor=counselor,
        case_number=f"CASE-{label}",
        status=CaseStatus.ACTIVE,
        counseling_method=CounselingMethod.IN_PERSON,
    )


def _create_confirmed_in_person_appointment(case: Case, *, scheduled_at) -> Appointment:
    return Appointment.objects.create(
        case=case,
        counselor=case.counselor,
        client=case.client,
        scheduled_at=scheduled_at,
        duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
        status=AppointmentStatus.CONFIRMED,
        confirmed_at=timezone.now(),
    )


class InPersonRoomCapacityTests(TestCase):
    def setUp(self):
        self.start = timezone.now().replace(
            hour=14, minute=0, second=0, microsecond=0
        ) + timedelta(days=7)
        self.counselor_a = _create_counselor("상담사A")
        self.counselor_b = _create_counselor("상담사B")

    def test_count_overlapping_in_person_appointments(self):
        client1 = _create_client("내담자1")
        client2 = _create_client("내담자2")
        case1 = _create_in_person_case(client1, self.counselor_a, "A")
        case2 = _create_in_person_case(client2, self.counselor_b, "B")
        _create_confirmed_in_person_appointment(case1, scheduled_at=self.start)
        _create_confirmed_in_person_appointment(
            case2,
            scheduled_at=self.start + timedelta(minutes=30),
        )
        overlap_count = count_overlapping_confirmed_in_person(
            scheduled_at=self.start + timedelta(minutes=40),
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
        )
        self.assertEqual(overlap_count, 2)

    def test_confirm_rejects_third_overlapping_in_person_appointment(self):
        client1 = _create_client("내담자1")
        client2 = _create_client("내담자2")
        client3 = _create_client("내담자3")
        case1 = _create_in_person_case(client1, self.counselor_a, "A")
        case2 = _create_in_person_case(client2, self.counselor_b, "B")
        case3 = _create_in_person_case(client3, self.counselor_a, "C")
        _create_confirmed_in_person_appointment(case1, scheduled_at=self.start)
        _create_confirmed_in_person_appointment(
            case2,
            scheduled_at=self.start + timedelta(minutes=30),
        )

        pending = Appointment.objects.create(
            case=case3,
            counselor=case3.counselor,
            client=case3.client,
            scheduled_at=self.start + timedelta(minutes=15),
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.PENDING,
        )

        with self.assertRaises(AppointmentServiceError) as ctx:
            confirm_appointment_with_zoom(pending, notify=False)
        self.assertEqual(str(ctx.exception), IN_PERSON_ROOM_CAPACITY_FULL_MESSAGE)

    def test_remote_appointment_not_limited_by_in_person_capacity(self):
        client1 = _create_client("대면1")
        client2 = _create_client("대면2")
        client3 = _create_client("비대면")
        case1 = _create_in_person_case(client1, self.counselor_a, "IP1")
        case2 = _create_in_person_case(client2, self.counselor_b, "IP2")
        remote_app = CounselingApplication.objects.create(
            client=client3,
            counseling_types=["진로상담"],
            reason="remote",
            counseling_method=CounselingMethod.REMOTE,
            status=ApplicationStatus.IN_PROGRESS,
        )
        case3 = Case.objects.create(
            application=remote_app,
            client=client3,
            counselor=self.counselor_a,
            case_number="CASE-R1",
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.REMOTE,
        )
        _create_confirmed_in_person_appointment(case1, scheduled_at=self.start)
        _create_confirmed_in_person_appointment(
            case2,
            scheduled_at=self.start + timedelta(minutes=30),
        )

        pending = Appointment.objects.create(
            case=case3,
            counselor=case3.counselor,
            client=case3.client,
            scheduled_at=self.start + timedelta(minutes=15),
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.PENDING,
        )

        ok, message = check_in_person_room_capacity(pending)
        self.assertTrue(ok, message)
