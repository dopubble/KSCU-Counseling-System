from datetime import datetime
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.reports.appointment_calendar import (
    HOST_COLORS,
    REMOTE_NO_ZOOM_COLORS,
    assign_zoom_hosts,
    appointment_overlaps_range,
    build_calendar_events,
    get_mock_calendar_events,
    CalendarInterval,
    parse_calendar_bound,
    resolve_calendar_zoom_host_display,
    zoom_host_label,
    _resolve_event_colors,
)
from apps.scheduling.zoom_hosts import host_id_for_email
from apps.scheduling.models import Appointment, AppointmentStatus


class AppointmentCalendarTests(TestCase):
    def test_zoom_host_label(self):
        self.assertEqual(zoom_host_label("host_01"), "Zoom 호스트 1번")
        self.assertEqual(zoom_host_label("host_02"), "Zoom 호스트 2번")

    @override_settings(
        CALENDAR_GCAL_UI=False,
        ZOOM_LICENSED_USERS="sscukscu@gmail.com,sedulife@mail.kcu.ac",
    )
    def test_hakyss_stored_host_uses_host03_yellow_not_blue(self):
        """구현정 등 hakyss 고정 → host_03(누런/주황), 블루 fallback 아님."""
        host_id, stored, expected, mismatch = resolve_calendar_zoom_host_display(
            is_remote=True,
            zoom_host_email="hakyss@mail.kcu.ac",
            expected_host_id="host_01",
            email_to_host_id=host_id_for_email,
        )
        self.assertEqual(host_id, "host_03")
        self.assertEqual(stored, "host_03")
        self.assertEqual(expected, "host_01")
        self.assertTrue(mismatch)
        colors = _resolve_event_colors(host_id=host_id, is_remote=True)
        self.assertEqual(colors["bg"], HOST_COLORS["host_03"]["bg"])
        self.assertNotEqual(colors["bg"], REMOTE_NO_ZOOM_COLORS["bg"])

    @override_settings(
        CALENDAR_GCAL_UI=False,
        ZOOM_LICENSED_USERS="sscukscu@gmail.com,sedulife@mail.kcu.ac",
    )
    def test_kim_sumi_stored_host01_stays_purple_not_blue(self):
        """김수미 DB host_01 → 보라(host_01), 알고리즘이 달라도 색은 DB 기준."""
        host_id, stored, expected, mismatch = resolve_calendar_zoom_host_display(
            is_remote=True,
            zoom_host_email="sscukscu@gmail.com",
            expected_host_id="host_02",
            email_to_host_id=host_id_for_email,
        )
        self.assertEqual(host_id, "host_01")
        self.assertEqual(stored, "host_01")
        self.assertTrue(mismatch)
        colors = _resolve_event_colors(host_id=host_id, is_remote=True)
        self.assertEqual(colors["bg"], HOST_COLORS["host_01"]["bg"])
        self.assertNotEqual(colors["bg"], REMOTE_NO_ZOOM_COLORS["bg"])

    def test_assign_zoom_hosts_splits_overlaps(self):
        start = timezone.now().replace(minute=0, second=0, microsecond=0)
        intervals = [
            CalendarInterval("a", start, start + timedelta(minutes=50), True),
            CalendarInterval(
                "b",
                start + timedelta(minutes=30),
                start + timedelta(minutes=80),
                True,
            ),
        ]
        with override_settings(ZOOM_HOST_POOL="host_01,host_02"):
            assignments = assign_zoom_hosts(intervals)
        self.assertEqual(assignments["a"], "host_01")
        self.assertEqual(assignments["b"], "host_02")

    def test_assign_zoom_hosts_splits_consecutive_hourly_with_buffer(self):
        """14:00·15:00 연속 타임 — 30분 버퍼로 서로 다른 호스트."""
        start = timezone.now().replace(hour=14, minute=0, second=0, microsecond=0)
        intervals = [
            CalendarInterval("a", start, start + timedelta(minutes=50), True),
            CalendarInterval(
                "b",
                start + timedelta(hours=1),
                start + timedelta(hours=1, minutes=50),
                True,
            ),
        ]
        with override_settings(
            ZOOM_HOST_POOL="host_01,host_02",
            ZOOM_HOST_BUFFER_MINUTES=30,
        ):
            assignments = assign_zoom_hosts(intervals)
        self.assertEqual(assignments["a"], "host_01")
        self.assertEqual(assignments["b"], "host_02")

    def test_assign_zoom_hosts_reuses_host_when_buffer_clear(self):
        """3시간 간격이면 동일 호스트 재사용 가능."""
        start = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        intervals = [
            CalendarInterval("a", start, start + timedelta(minutes=50), True),
            CalendarInterval(
                "b",
                start + timedelta(hours=3),
                start + timedelta(hours=3, minutes=50),
                True,
            ),
        ]
        with override_settings(
            ZOOM_HOST_POOL="host_01,host_02",
            ZOOM_HOST_BUFFER_MINUTES=30,
        ):
            assignments = assign_zoom_hosts(intervals)
        self.assertEqual(assignments["a"], "host_01")
        self.assertEqual(assignments["b"], "host_01")

    def test_assign_zoom_hosts_returns_none_when_pool_exhausted(self):
        start = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        slot_end = start + timedelta(minutes=50)
        intervals = [
            CalendarInterval("a", start, slot_end, True),
            CalendarInterval("b", start, slot_end, True),
            CalendarInterval("c", start, slot_end, True),
        ]
        with override_settings(
            ZOOM_HOST_POOL="host_01,host_02",
            ZOOM_HOST_BUFFER_MINUTES=30,
        ):
            assignments = assign_zoom_hosts(intervals)
        self.assertEqual(assignments["a"], "host_01")
        self.assertEqual(assignments["b"], "host_02")
        self.assertIsNone(assignments["c"])

    def test_mock_events_structure(self):
        events = get_mock_calendar_events()
        self.assertGreaterEqual(len(events), 2)
        first = events[0]
        self.assertIn("title", first)
        self.assertIn("extendedProps", first)
        self.assertEqual(first["extendedProps"]["zoom_host_id"], "host_01")

    def test_build_calendar_events_empty(self):
        events = build_calendar_events()
        self.assertEqual(events, [])

    def test_parse_calendar_bound_makes_naive_datetime_aware(self):
        parsed = parse_calendar_bound("2026-06-17")
        self.assertIsNotNone(parsed)
        self.assertIsNotNone(parsed.tzinfo)

    def test_confirmed_session1_client_visible_in_day_and_month_views(self):
        """1회기 확정(이현옥 유형) 일정이 FullCalendar 요청 범위에서 누락되지 않아야 한다."""
        apt = Appointment.objects.filter(
            client__name="이현옥",
            status=AppointmentStatus.CONFIRMED,
            session_number=1,
        ).first()
        if apt is None:
            self.skipTest("로컬 DB에 이현옥 1회기 확정 예약 없음")

        day_start = parse_calendar_bound("2026-06-17T00:00:00+09:00")
        day_end = parse_calendar_bound("2026-06-18T00:00:00+09:00")
        month_start = parse_calendar_bound("2026-06-01")
        month_end = parse_calendar_bound("2026-07-01")

        for start, end in ((day_start, day_end), (month_start, month_end)):
            events = build_calendar_events(start=start, end=end)
            names = [event["extendedProps"]["client_name"] for event in events]
            self.assertIn("이현옥", names, msg=f"range {start} ~ {end}")

        start_at = timezone.localtime(apt.scheduled_at)
        end_at = start_at + timedelta(minutes=apt.duration_minutes or 50)
        self.assertTrue(
            appointment_overlaps_range(
                start_at, end_at, range_start=day_start, range_end=day_end
            )
        )

    def test_pending_appointment_excluded_from_calendar(self):
        pending_count = Appointment.objects.filter(
            status=AppointmentStatus.PENDING
        ).count()
        if pending_count == 0:
            self.skipTest("PENDING 예약 없음")

        start = parse_calendar_bound("2026-06-01")
        end = parse_calendar_bound("2026-07-01")
        events = build_calendar_events(start=start, end=end)
        event_ids = {event["id"] for event in events}
        pending_ids = {
            str(pk)
            for pk in Appointment.objects.filter(status=AppointmentStatus.PENDING).values_list(
                "pk", flat=True
            )
        }
        self.assertFalse(event_ids & pending_ids)

    def test_completed_appointment_included_in_calendar(self):
        client_user = User.objects.create_user(
            email="completed-cal@example.com",
            password="pass",
            name="완료내담",
            role=UserRole.CLIENT,
        )
        counselor = User.objects.create_user(
            email="counselor-cal@example.com",
            password="pass",
            name="상담사",
            role=UserRole.COUNSELOR,
        )
        application = CounselingApplication.objects.create(
            client=client_user,
            counseling_types=["개인상담"],
            reason="test",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.IN_PROGRESS,
        )
        case = Case.objects.create(
            application=application,
            client=client_user,
            counselor=counselor,
            case_number="CASE-CAL-COMPLETED",
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.IN_PERSON,
        )
        scheduled_at = timezone.make_aware(datetime(2026, 7, 1, 10, 0))
        completed = Appointment.objects.create(
            case=case,
            counselor=counselor,
            client=client_user,
            scheduled_at=scheduled_at,
            status=AppointmentStatus.COMPLETED,
            session_number=3,
            confirmed_at=timezone.now(),
        )

        day_start = parse_calendar_bound("2026-07-01T00:00:00+09:00")
        day_end = parse_calendar_bound("2026-07-02T00:00:00+09:00")
        events = build_calendar_events(start=day_start, end=day_end)
        event_ids = {event["id"] for event in events}
        self.assertIn(str(completed.pk), event_ids)

    def test_fullcalendar_utc_month_range_includes_june_17(self):
        """FullCalendar month view가 UTC ISO로 전달해도 6/17 확정 일정이 포함되어야 한다."""
        apt = Appointment.objects.filter(
            client__name="이현옥",
            status=AppointmentStatus.CONFIRMED,
            session_number=1,
        ).first()
        if apt is None:
            self.skipTest("로컬 DB에 이현옥 1회기 확정 예약 없음")

        ranges = (
            ("seoul", "2026-05-31T00:00:00+09:00", "2026-07-06T00:00:00+09:00"),
            ("utc", "2026-05-30T15:00:00.000Z", "2026-07-05T15:00:00.000Z"),
        )
        for label, start_raw, end_raw in ranges:
            events = build_calendar_events(
                start=parse_calendar_bound(start_raw),
                end=parse_calendar_bound(end_raw),
            )
            names = [event["extendedProps"]["client_name"] for event in events]
            self.assertIn(
                "이현옥",
                names,
                msg=f"{label} range {start_raw} ~ {end_raw}",
            )

    def test_calendar_events_api_returns_confirmed_client(self):
        apt = Appointment.objects.filter(
            client__name="이현옥",
            status=AppointmentStatus.CONFIRMED,
        ).first()
        if apt is None:
            self.skipTest("로컬 DB에 이현옥 확정 예약 없음")

        admin = User.objects.create_user(
            email="admin-calendar@example.com",
            password="Testpass123!",
            name="관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        client = Client()
        client.force_login(admin)
        response = client.get(
            reverse("admin_panel:appointment_calendar_events"),
            {
                "start": "2026-06-17T00:00:00+09:00",
                "end": "2026-06-18T00:00:00+09:00",
            },
        )
        self.assertEqual(response.status_code, 200)
        names = [
            event["extendedProps"]["client_name"] for event in response.json()["events"]
        ]
        self.assertIn("이현옥", names)
