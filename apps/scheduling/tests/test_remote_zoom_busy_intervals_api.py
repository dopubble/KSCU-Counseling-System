from datetime import timedelta

from django.test import Client as HttpClient, TestCase, override_settings
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
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus


class RemoteZoomBusyIntervalsApiTests(TestCase):
    def setUp(self):
        self.http = HttpClient()
        self.start = timezone.now().replace(
            hour=10, minute=0, second=0, microsecond=0
        ) + timedelta(days=5)
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
            case_number="CASE-API-1",
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.REMOTE,
        )
        Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.start,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.CONFIRMED,
            confirmed_at=timezone.now(),
        )

    @override_settings(ZOOM_LICENSED_USERS="a@example.com,b@example.com")
    def test_api_returns_busy_intervals_for_logged_in_user(self):
        self.http.force_login(self.client_user)
        url = reverse("scheduling:remote_zoom_busy_intervals")
        response = self.http.get(
            url,
            {
                "start": self.start.strftime("%Y-%m-%dT00:00:00"),
                "end": (self.start + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59"),
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["capacity"], 2)
        self.assertEqual(len(payload["intervals"]), 1)

    def test_api_requires_login(self):
        url = reverse("scheduling:remote_zoom_busy_intervals")
        response = self.http.get(
            url,
            {
                "start": self.start.strftime("%Y-%m-%dT00:00:00"),
                "end": (self.start + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59"),
            },
        )
        self.assertEqual(response.status_code, 302)
