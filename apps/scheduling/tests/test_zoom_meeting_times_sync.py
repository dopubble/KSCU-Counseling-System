"""Zoom 회의 시간 파싱·동기화 테스트."""

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

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
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.services import sync_zoom_meeting_times
from apps.scheduling.utils import parse_zoom_meeting_start_datetime
from apps.sessions_app.models import ZoomMeeting


class ParseZoomMeetingStartTests(TestCase):
    def test_parses_utc_suffix(self):
        data = {
            "start_time": "2026-07-02T07:00:00Z",
            "timezone": "Asia/Seoul",
            "duration": 50,
        }
        parsed = parse_zoom_meeting_start_datetime(data)
        self.assertIsNotNone(parsed)
        local = timezone.localtime(parsed)
        self.assertEqual(local.hour, 16)
        self.assertEqual(local.day, 2)

    def test_parses_local_time_with_timezone_field(self):
        data = {
            "start_time": "2026-07-02T17:00:00",
            "timezone": "Asia/Seoul",
        }
        parsed = parse_zoom_meeting_start_datetime(data)
        self.assertIsNotNone(parsed)
        local = timezone.localtime(parsed)
        self.assertEqual(local.strftime("%Y-%m-%d %H:%M"), "2026-07-02 17:00")


@override_settings(
    ZOOM_ACCOUNT_ID="acc",
    ZOOM_CLIENT_ID="cid",
    ZOOM_CLIENT_SECRET="sec",
    TIME_ZONE="Asia/Seoul",
)
class SyncZoomMeetingTimesTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="client@example.com",
            password="pass12345",
            name="고혜숙",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.counselor = User.objects.create_user(
            email="counselor@example.com",
            password="pass12345",
            name="김상연",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.get_or_create(user=self.counselor, defaults={"cohort": 1})
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["진로상담"],
            reason="test",
            counseling_method=CounselingMethod.REMOTE,
            status=ApplicationStatus.IN_PROGRESS,
        )
        self.case = Case.objects.create(
            application=application,
            client=self.client_user,
            counselor=self.counselor,
            case_number="CASE-ZOOM-SYNC",
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.REMOTE,
        )
        kst = ZoneInfo("Asia/Seoul")
        self.scheduled_at = timezone.make_aware(
            datetime(2026, 7, 2, 17, 0),
            kst,
        )
        self.appointment = Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            duration_minutes=50,
            status=AppointmentStatus.CONFIRMED,
            session_number=2,
            confirmed_at=timezone.now(),
        )
        self.zoom = ZoomMeeting.objects.create(
            appointment=self.appointment,
            zoom_meeting_id="12345678901",
            join_url="https://zoom.us/j/12345678901",
        )

    @patch("apps.scheduling.services.get_zoom_meeting")
    def test_dry_run_lists_mismatch(self, get_meeting_mock):
        get_meeting_mock.return_value = {
            "start_time": "2026-07-02T07:00:00Z",
            "timezone": "Asia/Seoul",
            "duration": 50,
            "join_url": "https://zoom.us/j/12345678901",
        }
        in_sync, updated, _, failed, mismatches, errors = sync_zoom_meeting_times(
            dry_run=True
        )
        self.assertEqual(in_sync, 0)
        self.assertEqual(updated, 0)
        self.assertEqual(failed, 0)
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["db_local"], "2026-07-02 17:00")
        self.assertEqual(mismatches[0]["zoom_local"], "2026-07-02 16:00")
        self.assertEqual(errors, [])

    @patch("apps.scheduling.services._sync_zoom_meeting_from_api")
    @patch("apps.scheduling.services.update_zoom_meeting")
    @patch("apps.scheduling.services.get_zoom_meeting")
    def test_apply_updates_mismatch(
        self,
        get_meeting_mock,
        update_mock,
        sync_from_api_mock,
    ):
        get_meeting_mock.return_value = {
            "start_time": "2026-07-02T07:00:00Z",
            "timezone": "Asia/Seoul",
            "duration": 50,
            "join_url": "https://zoom.us/j/12345678901",
        }
        update_mock.return_value = {}
        sync_from_api_mock.return_value = self.zoom

        in_sync, updated, _, failed, mismatches, errors = sync_zoom_meeting_times(
            dry_run=False
        )
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(updated, 1)
        self.assertEqual(failed, 0)
        update_mock.assert_called_once()
        self.assertEqual(errors, [])

    @patch("apps.scheduling.services.get_zoom_meeting")
    def test_in_sync_skipped(self, get_meeting_mock):
        get_meeting_mock.return_value = {
            "start_time": "2026-07-02T08:00:00Z",
            "timezone": "Asia/Seoul",
            "duration": 50,
            "join_url": "https://zoom.us/j/12345678901",
        }
        in_sync, updated, _, failed, mismatches, errors = sync_zoom_meeting_times(
            dry_run=False
        )
        self.assertEqual(in_sync, 1)
        self.assertEqual(updated, 0)
        self.assertEqual(mismatches, [])
        self.assertEqual(errors, [])
