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
from apps.counseling.services import (
    CounselorSessionCardView,
    _resolve_appointment_zoom_url,
    build_case_session_cards,
)
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.services import _create_zoom_meeting_for_appointment
from apps.scheduling.zoom_links import (
    appointment_zoom_link_is_locked,
    resolve_appointment_zoom_join_url,
    sanitize_participant_zoom_url,
    sync_case_zoom_meeting_url,
    verify_counselor_zoom_join_policy,
    ZoomLaunchPolicyError,
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

    def test_counselor_resolver_uses_join_url_even_when_start_url_stored(self):
        from apps.scheduling.zoom_links import resolve_appointment_zoom_counselor_url

        zm = ZoomMeeting.objects.get(appointment=self.appointment)
        zm.start_url = "https://zoom.us/s/81733363550"
        zm.save(update_fields=["start_url"])
        apt = Appointment.objects.select_related("zoom_meeting").get(pk=self.appointment.pk)
        self.assertEqual(
            resolve_appointment_zoom_counselor_url(apt, self.case),
            "https://zoom.us/j/81733363550",
        )
        self.assertEqual(
            resolve_appointment_zoom_join_url(apt, self.case),
            "https://zoom.us/j/81733363550",
        )

    def test_appointment_counselor_host_key_override(self):
        from apps.scheduling.zoom_links import appointment_counselor_host_key

        zm = ZoomMeeting.objects.get(appointment=self.appointment)
        zm.counselor_host_key = "877273"
        zm.save(update_fields=["counselor_host_key"])
        apt = Appointment.objects.select_related("zoom_meeting").get(pk=self.appointment.pk)
        self.assertEqual(appointment_counselor_host_key(apt), "877273")

    def test_sanitize_participant_zoom_url_rejects_host_urls(self):
        self.assertEqual(
            sanitize_participant_zoom_url("https://zoom.us/j/81733363550"),
            "https://zoom.us/j/81733363550",
        )
        self.assertEqual(sanitize_participant_zoom_url("https://zoom.us/s/host"), "")
        self.assertEqual(
            sanitize_participant_zoom_url("https://zoom.us/j/1?zak=token"),
            "",
        )

    def test_verify_counselor_zoom_join_policy(self):
        verify_counselor_zoom_join_policy()

    def test_counselor_session_card_uses_join_url_not_start_url(self):
        self.case.total_sessions = 3
        self.case.save(update_fields=["total_sessions"])
        cards = build_case_session_cards(self.case)
        card = next(c for c in cards if c.session_number == 1)
        view = CounselorSessionCardView(card)
        self.assertIn("/j/", view.zoom_url)
        self.assertNotIn("/s/", view.zoom_url)

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

    @patch("apps.scheduling.services.get_zoom_meeting")
    @patch("apps.scheduling.services.update_zoom_meeting_participant_settings")
    @patch("apps.scheduling.services.create_zoom_meeting")
    def test_create_zoom_meeting_notifies_when_link_changes(
        self, mock_create, mock_patch, mock_get
    ):
        meeting_payload = {
            "id": "99999999999",
            "join_url": "https://zoom.us/j/99999999999",
            "start_url": "https://zoom.us/s/new",
            "password": "",
        }
        mock_create.return_value = meeting_payload
        mock_patch.return_value = {}
        mock_get.return_value = meeting_payload
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

    def test_appointment_zoom_link_is_locked_when_join_url_and_meeting_id_exist(self):
        self.assertTrue(appointment_zoom_link_is_locked(self.appointment))
        zm = ZoomMeeting.objects.get(appointment=self.appointment)
        zm.join_url = ""
        zm.zoom_meeting_id = ""
        zm.save(update_fields=["join_url", "zoom_meeting_id"])
        fresh = Appointment.objects.select_related("zoom_meeting").get(pk=self.appointment.pk)
        self.assertFalse(appointment_zoom_link_is_locked(fresh))
