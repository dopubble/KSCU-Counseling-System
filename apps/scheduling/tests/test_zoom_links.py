"""Zoom 링크 resolver·동기화·변경 알림."""

from unittest.mock import patch

from datetime import datetime

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User, UserRole
from apps.counseling.emailing import (
    send_appointment_confirmation_notification,
    send_appointment_zoom_link_updated_notification,
)
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.counseling.services import _resolve_appointment_zoom_url
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.services import _create_zoom_meeting_for_appointment
from apps.scheduling.zoom_links import (
    resolve_appointment_zoom_join_url,
    sync_case_zoom_meeting_url,
)
from apps.sessions_app.models import ZoomMeeting


class ZoomLinkResolverTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="client@example.com",
            password="pass",
            name="내담자",
            role=UserRole.CLIENT,
        )
        self.counselor = User.objects.create_user(
            email="counselor@example.com",
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
            case_number="CASE-ZOOM-LINK",
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.REMOTE,
            zoom_meeting_url="https://zoom.us/j/stale-case",
        )
        self.appointment = Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=timezone.make_aware(datetime(2026, 6, 26, 11, 0)),
            status=AppointmentStatus.CONFIRMED,
            session_number=1,
            confirmed_at=timezone.now(),
        )
        ZoomMeeting.objects.create(
            appointment=self.appointment,
            zoom_meeting_id="81733363550",
            join_url="https://zoom.us/j/81733363550",
            start_url="https://zoom.us/s/host",
            zoom_host_email="sscukscu@gmail.com",
        )

    def test_resolver_prefers_appointment_join_url_over_stale_case_url(self):
        url = resolve_appointment_zoom_join_url(self.appointment, self.case)
        self.assertEqual(url, "https://zoom.us/j/81733363550")
        self.assertEqual(
            _resolve_appointment_zoom_url(self.appointment, self.case),
            url,
        )

    def test_sync_case_zoom_meeting_url_updates_case_from_appointment(self):
        sync_case_zoom_meeting_url(
            self.appointment,
            join_url="https://zoom.us/j/81733363550",
        )
        self.case.refresh_from_db()
        self.assertEqual(self.case.zoom_meeting_url, "https://zoom.us/j/81733363550")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_ASYNC=False,
    )
    def test_confirmation_email_uses_appointment_join_url(self):
        send_appointment_confirmation_notification(self.appointment)
        from django.core import mail

        body = mail.outbox[0].body
        self.assertIn("https://zoom.us/j/81733363550", body)
        self.assertNotIn("stale-case", body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_ASYNC=False,
    )
    def test_zoom_link_updated_notification_to_client_and_counselor(self):
        sent = send_appointment_zoom_link_updated_notification(
            self.appointment,
            previous_url="https://zoom.us/j/old",
        )
        self.assertTrue(sent)
        from django.core import mail

        self.assertEqual(len(mail.outbox), 1)
        recipients = set(mail.outbox[0].to)
        self.assertIn("client@example.com", recipients)
        self.assertIn("counselor@example.com", recipients)
        self.assertIn("81733363550", mail.outbox[0].body)

    @patch("apps.scheduling.services.create_zoom_meeting")
    def test_create_zoom_meeting_notifies_when_link_changes(self, mock_create):
        mock_create.return_value = {
            "id": "99999999999",
            "join_url": "https://zoom.us/j/99999999999",
            "start_url": "https://zoom.us/s/new",
            "password": "",
        }
        with override_settings(
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
            EMAIL_ASYNC=False,
        ):
            _create_zoom_meeting_for_appointment(
                self.appointment,
                host_user_email="sedulife@mail.kcu.ac",
                notify_link_change=True,
            )
        from django.core import mail

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("99999999999", mail.outbox[0].body)
        self.case.refresh_from_db()
        self.assertEqual(self.case.zoom_meeting_url, "https://zoom.us/j/99999999999")
