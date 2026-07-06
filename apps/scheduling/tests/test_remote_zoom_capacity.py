"""비대면 Zoom 동시 예약 용량 검증 테스트."""

from datetime import timedelta
from unittest.mock import patch

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
    check_remote_zoom_capacity,
    count_overlapping_confirmed_remote,
)
from apps.scheduling.services import (
    AppointmentServiceError,
    confirm_appointment_with_zoom,
    reschedule_confirmed_appointment,
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


def _create_confirmed_remote_appointment(
    case: Case,
    *,
    scheduled_at,
    counselor: User | None = None,
) -> Appointment:
    return Appointment.objects.create(
        case=case,
        counselor=counselor or case.counselor,
        client=case.client,
        scheduled_at=scheduled_at,
        duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
        status=AppointmentStatus.CONFIRMED,
        confirmed_at=timezone.now(),
    )


@override_settings(ZOOM_LICENSED_USERS="host1@example.com,host2@example.com")
class RemoteZoomCapacityTests(TestCase):
    def setUp(self):
        self.start = timezone.now().replace(
            hour=14, minute=0, second=0, microsecond=0
        ) + timedelta(days=7)
        self.counselor_a = _create_counselor("상담사A")
        self.counselor_b = _create_counselor("상담사B")

    def test_count_overlapping_uses_fifty_minute_default_duration(self):
        client1 = _create_client("내담자1")
        client2 = _create_client("내담자2")
        case1 = _create_remote_case(client1, self.counselor_a, "A")
        case2 = _create_remote_case(client2, self.counselor_b, "B")
        _create_confirmed_remote_appointment(
            case1,
            scheduled_at=self.start,
        )
        _create_confirmed_remote_appointment(
            case2,
            scheduled_at=self.start + timedelta(minutes=30),
        )
        overlap_count = count_overlapping_confirmed_remote(
            scheduled_at=self.start + timedelta(minutes=40),
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
        )
        self.assertEqual(overlap_count, 2)

    def test_confirm_rejects_third_overlapping_remote_appointment(self):
        client1 = _create_client("내담자1")
        client2 = _create_client("내담자2")
        client3 = _create_client("내담자3")
        counselor_c = _create_counselor("상담사C")
        case1 = _create_remote_case(client1, self.counselor_a, "A")
        case2 = _create_remote_case(client2, self.counselor_b, "B")
        case3 = _create_remote_case(client3, counselor_c, "C")
        _create_confirmed_remote_appointment(case1, scheduled_at=self.start)
        _create_confirmed_remote_appointment(case2, scheduled_at=self.start)

        pending = Appointment.objects.create(
            case=case3,
            counselor=case3.counselor,
            client=case3.client,
            scheduled_at=self.start,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.PENDING,
        )

        with patch(
            "apps.scheduling.services._create_zoom_meeting_for_appointment"
        ) as mock_zoom:
            with self.assertRaises(AppointmentServiceError) as ctx:
                confirm_appointment_with_zoom(pending, notify=False)
            self.assertEqual(str(ctx.exception), REMOTE_ZOOM_CAPACITY_FULL_MESSAGE)
            mock_zoom.assert_not_called()

    def test_confirm_rejects_same_start_when_both_hosts_busy(self):
        client1 = _create_client("내담자1")
        client2 = _create_client("내담자2")
        client3 = _create_client("내담자3")
        counselor_c = _create_counselor("상담사C")
        case1 = _create_remote_case(client1, self.counselor_a, "A")
        case2 = _create_remote_case(client2, self.counselor_b, "B")
        case3 = _create_remote_case(client3, counselor_c, "C")
        _create_confirmed_remote_appointment(case1, scheduled_at=self.start)
        _create_confirmed_remote_appointment(case2, scheduled_at=self.start)

        pending = Appointment.objects.create(
            case=case3,
            counselor=case3.counselor,
            client=case3.client,
            scheduled_at=self.start,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.PENDING,
        )

        with patch(
            "apps.scheduling.services._create_zoom_meeting_for_appointment"
        ) as mock_zoom:
            with self.assertRaises(AppointmentServiceError) as ctx:
                confirm_appointment_with_zoom(pending, notify=False)
            self.assertEqual(str(ctx.exception), REMOTE_ZOOM_CAPACITY_FULL_MESSAGE)
            mock_zoom.assert_not_called()

    def test_in_person_appointment_not_limited_by_zoom_capacity(self):
        client = _create_client("대면내담자")
        application = CounselingApplication.objects.create(
            client=client,
            counseling_types=["진로상담"],
            reason="대면",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.IN_PROGRESS,
        )
        case = Case.objects.create(
            application=application,
            client=client,
            counselor=self.counselor_a,
            case_number="CASE-IN-PERSON",
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.IN_PERSON,
        )
        pending = Appointment.objects.create(
            case=case,
            counselor=case.counselor,
            client=case.client,
            scheduled_at=self.start,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.PENDING,
        )
        appointment, zoom = confirm_appointment_with_zoom(pending, notify=False)
        self.assertEqual(appointment.status, AppointmentStatus.CONFIRMED)
        self.assertIsNone(zoom)

    def test_reschedule_allows_move_when_excluding_self(self):
        client1 = _create_client("내담자1")
        client2 = _create_client("내담자2")
        case1 = _create_remote_case(client1, self.counselor_a, "A")
        case2 = _create_remote_case(client2, self.counselor_b, "B")
        _create_confirmed_remote_appointment(case1, scheduled_at=self.start)
        moving = _create_confirmed_remote_appointment(
            case2,
            scheduled_at=self.start + timedelta(hours=2),
        )
        new_time = self.start + timedelta(minutes=30)

        ok, message = check_remote_zoom_capacity(
            moving,
            scheduled_at=new_time,
            exclude_appointment_id=moving.pk,
        )
        self.assertTrue(ok, message)

        with patch("apps.scheduling.services.update_zoom_meeting", return_value={}):
            updated, warning = reschedule_confirmed_appointment(
                moving,
                new_scheduled_at=new_time,
                skip_availability=True,
            )
        self.assertEqual(updated.scheduled_at, new_time)
        self.assertIsNone(warning)

    def test_reschedule_reassigns_zoom_host_when_slot_overlaps_peer(self):
        client1 = _create_client("내담자1")
        client2 = _create_client("내담자2")
        case1 = _create_remote_case(client1, self.counselor_a, "A")
        case2 = _create_remote_case(client2, self.counselor_b, "B")
        anchor = _create_confirmed_remote_appointment(case1, scheduled_at=self.start)
        moving = _create_confirmed_remote_appointment(
            case2,
            scheduled_at=self.start + timedelta(hours=2),
        )

        from apps.scheduling import services as scheduling_services
        from apps.sessions_app.models import ZoomMeeting

        ZoomMeeting.objects.create(
            appointment=anchor,
            zoom_meeting_id="11111111111",
            join_url="https://zoom.us/j/111",
            zoom_host_email="host1@example.com",
        )
        ZoomMeeting.objects.create(
            appointment=moving,
            zoom_meeting_id="22222222222",
            join_url="https://zoom.us/j/222",
            zoom_host_email="host1@example.com",
        )

        new_zoom = ZoomMeeting(
            appointment=moving,
            zoom_meeting_id="33333333333",
            join_url="https://zoom.us/j/333",
            zoom_host_email="host2@example.com",
        )

        with (
            patch.object(
                scheduling_services,
                "resolve_zoom_host_email_for_appointment",
                return_value="host2@example.com",
            ),
            patch.object(
                scheduling_services,
                "_create_zoom_meeting_for_appointment",
                return_value=(new_zoom, new_zoom.join_url),
            ) as create_mock,
            patch.object(scheduling_services, "delete_zoom_meeting") as delete_mock,
            patch.object(scheduling_services, "update_zoom_meeting") as update_mock,
            patch.object(
                scheduling_services,
                "fix_mismatched_zoom_host_assignments",
            ),
        ):
            updated, warning = reschedule_confirmed_appointment(
                moving,
                new_scheduled_at=self.start,
                skip_availability=True,
            )

        self.assertEqual(updated.scheduled_at, self.start)
        self.assertIsNone(warning)
        create_mock.assert_called_once()
        self.assertEqual(create_mock.call_args.kwargs["host_user_email"], "host2@example.com")
        delete_mock.assert_called_once_with("22222222222")
        update_mock.assert_not_called()


@override_settings(
    ZOOM_LICENSED_USERS="host1@example.com,host2@example.com,host3@example.com"
)
class RemoteZoomStaggeredHostTests(TestCase):
    """동시간대 상한 2 + host_03 엇갈림 배정."""

    def setUp(self):
        from apps.scheduling.models import RemoteZoomSchedulingSettings

        RemoteZoomSchedulingSettings.objects.update_or_create(
            pk=RemoteZoomSchedulingSettings.SETTINGS_PK,
            defaults={"simultaneous_session_capacity": 2},
        )
        self.base = timezone.now().replace(
            hour=11, minute=0, second=0, microsecond=0
        ) + timedelta(days=14)
        self.counselor_a = _create_counselor("상담사A")
        self.counselor_b = _create_counselor("상담사B")
        self.counselor_c = _create_counselor("상담사C")

    def test_10am_allowed_when_two_confirmed_at_11am(self):
        case1 = _create_remote_case(_create_client("내담자1"), self.counselor_a, "A")
        case2 = _create_remote_case(_create_client("내담자2"), self.counselor_b, "B")
        _create_confirmed_remote_appointment(case1, scheduled_at=self.base)
        _create_confirmed_remote_appointment(case2, scheduled_at=self.base)

        slot_10 = self.base - timedelta(hours=1)
        ok, message = check_remote_zoom_capacity(
            Appointment(
                case=case1,
                counselor=case1.counselor,
                client=case1.client,
                scheduled_at=slot_10,
                duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
                status=AppointmentStatus.PENDING,
            ),
            scheduled_at=slot_10,
        )
        self.assertTrue(ok, message)

    def test_third_11am_blocked_even_with_three_licensed_hosts(self):
        case1 = _create_remote_case(_create_client("내담자1"), self.counselor_a, "A")
        case2 = _create_remote_case(_create_client("내담자2"), self.counselor_b, "B")
        case3 = _create_remote_case(_create_client("내담자3"), self.counselor_c, "C")
        _create_confirmed_remote_appointment(case1, scheduled_at=self.base)
        _create_confirmed_remote_appointment(case2, scheduled_at=self.base)

        pending = Appointment.objects.create(
            case=case3,
            counselor=case3.counselor,
            client=case3.client,
            scheduled_at=self.base,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.PENDING,
        )
        ok, message = check_remote_zoom_capacity(pending, scheduled_at=self.base)
        self.assertFalse(ok)
        self.assertEqual(message, REMOTE_ZOOM_CAPACITY_FULL_MESSAGE)

    def test_admin_can_raise_simultaneous_capacity(self):
        from apps.scheduling.models import RemoteZoomSchedulingSettings
        from apps.scheduling.zoom_scheduling_settings import (
            get_remote_zoom_simultaneous_capacity,
        )

        settings_row = RemoteZoomSchedulingSettings.objects.get(
            pk=RemoteZoomSchedulingSettings.SETTINGS_PK
        )
        settings_row.simultaneous_session_capacity = 3
        settings_row.save()
        self.assertEqual(get_remote_zoom_simultaneous_capacity(), 3)
