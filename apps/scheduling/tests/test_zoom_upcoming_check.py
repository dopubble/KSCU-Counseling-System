"""check_deploy_safety · check_zoom_upcoming management commands."""

from datetime import datetime, timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User, UserRole
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.sessions_app.models import ZoomMeeting


class CheckZoomUpcomingCommandTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="upcoming-client@example.com",
            password="pass",
            name="내담자",
            role=UserRole.CLIENT,
        )
        self.counselor = User.objects.create_user(
            email="upcoming-counselor@example.com",
            password="pass",
            name="상담사",
            role=UserRole.COUNSELOR,
        )
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["개인상담"],
            reason="test",
            counseling_method=CounselingMethod.REMOTE,
            status=ApplicationStatus.IN_PROGRESS,
        )
        self.case = Case.objects.create(
            application=application,
            client=self.client_user,
            counselor=self.counselor,
            case_number="CASE-ZOOM-UPCOMING",
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.REMOTE,
        )
        self.appointment = Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=timezone.now() + timedelta(hours=2),
            status=AppointmentStatus.CONFIRMED,
            session_number=1,
            confirmed_at=timezone.now(),
        )
        ZoomMeeting.objects.create(
            appointment=self.appointment,
            zoom_meeting_id="81733363550",
            join_url="https://zoom.us/j/81733363550",
            start_url="https://zoom.us/s/81733363550",
            zoom_host_email="host@example.com",
        )

    @override_settings(ZOOM_HOST_KEY="123456")
    def test_check_zoom_upcoming_ok(self):
        out = StringIO()
        call_command("check_zoom_upcoming", "--days", "1", stdout=out)
        self.assertIn("OK", out.getvalue())

    def test_check_deploy_safety_zoom_policy(self):
        out = StringIO()
        call_command("check_deploy_safety", stdout=out)
        self.assertIn("Zoom join_url 정책 ok", out.getvalue())

    def test_check_zoom_upcoming_strict_fails_without_join(self):
        zm = ZoomMeeting.objects.get(appointment=self.appointment)
        zm.join_url = ""
        zm.zoom_meeting_id = ""
        zm.save(update_fields=["join_url", "zoom_meeting_id"])
        with self.assertRaises(CommandError):
            call_command("check_zoom_upcoming", "--days", "1", "--strict")
