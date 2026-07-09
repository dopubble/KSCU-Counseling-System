"""Admin HTTP: ValidationError from save_model must not return 500."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CounselorProfile, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.services import AppointmentServiceError


def _create_client(name: str = "내담자") -> "get_user_model()":
    User = get_user_model()
    return User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.CLIENT,
        status=UserStatus.ACTIVE,
    )


def _create_counselor(name: str = "상담사"):
    User = get_user_model()
    user = User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.COUNSELOR,
        status=UserStatus.ACTIVE,
    )
    CounselorProfile.objects.get_or_create(user=user, defaults={"cohort": 1})
    return user


def _create_remote_case(client, counselor, label: str) -> Case:
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


@override_settings(ZOOM_LICENSED_USERS="host1@example.com,host2@example.com")
class AppointmentAdminHttpValidationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin_user = User.objects.create_superuser(
            email="admin@example.com",
            password="pass12345",
            name="Admin",
        )
        self.counselor = _create_counselor()
        self.client_user = _create_client()
        self.case = _create_remote_case(self.client_user, self.counselor, "HTTP")
        self.scheduled_at = timezone.now().replace(
            hour=14, minute=0, second=0, microsecond=0
        ) + timedelta(days=7)
        self.http = Client()
        self.http.force_login(self.admin_user)

    @patch(
        "apps.scheduling.admin.confirm_appointment_with_zoom",
        side_effect=AppointmentServiceError("호스트 없음"),
    )
    def test_zoom_failure_returns_form_not_500(self, _mock_confirm):
        url = reverse("admin:scheduling_appointment_add")
        response = self.http.post(
            url,
            {
                "case": str(self.case.pk),
                "client": str(self.client_user.pk),
                "counselor": str(self.counselor.pk),
                "scheduled_at_0": self.scheduled_at.date().isoformat(),
                "scheduled_at_1": self.scheduled_at.strftime("%H:%M:%S"),
                "duration_minutes": DEFAULT_APPOINTMENT_DURATION_MINUTES,
                "status": AppointmentStatus.CONFIRMED,
                "session_number": 1,
                "request_message": "",
                "cancel_reason": "",
            },
        )
        self.assertNotEqual(response.status_code, 500, response.content[:500])
        self.assertFalse(Appointment.objects.filter(case=self.case).exists())

    @patch("apps.scheduling.services.create_zoom_meeting")
    @patch("apps.scheduling.services.get_zoom_meeting")
    @patch("apps.scheduling.services.update_zoom_meeting_participant_settings")
    def test_zoom_success_creates_meeting(
        self,
        _mock_patch_settings,
        mock_get_zoom,
        mock_create_zoom_api,
    ):
        mock_create_zoom_api.return_value = {
            "id": "111",
            "join_url": "https://zoom.us/j/111",
            "start_url": "",
            "password": "",
        }
        mock_get_zoom.return_value = mock_create_zoom_api.return_value

        url = reverse("admin:scheduling_appointment_add")
        response = self.http.post(
            url,
            {
                "case": str(self.case.pk),
                "client": str(self.client_user.pk),
                "counselor": str(self.counselor.pk),
                "scheduled_at_0": self.scheduled_at.date().isoformat(),
                "scheduled_at_1": self.scheduled_at.strftime("%H:%M:%S"),
                "duration_minutes": DEFAULT_APPOINTMENT_DURATION_MINUTES,
                "status": AppointmentStatus.CONFIRMED,
                "session_number": 1,
                "request_message": "",
                "cancel_reason": "",
            },
        )
        self.assertIn(response.status_code, (302, 200))
        appointment = Appointment.objects.get(case=self.case)
        self.assertEqual(appointment.status, AppointmentStatus.CONFIRMED)
        self.assertTrue(
            appointment.zoom_meeting.join_url.startswith("https://zoom.us/j/")
        )
