from unittest.mock import patch

from django.test import SimpleTestCase

from apps.counseling.services import _is_zoom_host_url, _resolve_appointment_zoom_url
from apps.scheduling.services import _zoom_meeting_record_is_usable
from apps.scheduling.utils import (
    ZoomAPIError,
    _zoom_meeting_settings,
    pick_meeting_launch_url,
)

class ZoomMeetingSettingsTests(SimpleTestCase):
    def test_zoom_meeting_settings_for_participant_join(self):
        settings = _zoom_meeting_settings()
        self.assertTrue(settings["join_before_host"])
        self.assertFalse(settings["waiting_room"])
        self.assertEqual(settings["alternative_hosts"], "")

    def test_pick_meeting_launch_url_prefers_join(self):
        url = pick_meeting_launch_url(
            {
                "join_url": "https://zoom.us/j/123",
                "start_url": "https://zoom.us/s/456",
            }
        )
        self.assertEqual(url, "https://zoom.us/j/123")

    def test_is_zoom_host_url(self):
        self.assertTrue(_is_zoom_host_url("https://zoom.us/s/abc"))
        self.assertTrue(_is_zoom_host_url("https://zoom.us/j/123?zak=token"))
        self.assertFalse(_is_zoom_host_url("https://zoom.us/j/123"))

    def test_is_zoom_host_key_configured(self):
        from apps.scheduling.utils import get_zoom_host_key, is_zoom_host_key_configured

        with self.settings(ZOOM_HOST_KEY="123456"):
            self.assertTrue(is_zoom_host_key_configured())
            self.assertEqual(get_zoom_host_key(), "123456")
        with self.settings(ZOOM_HOST_KEY="12345"):
            self.assertFalse(is_zoom_host_key_configured())
        with self.settings(ZOOM_HOST_KEY=""):
            self.assertFalse(is_zoom_host_key_configured())

    def test_resolve_appointment_zoom_url_never_returns_start_url(self):
        class Zoom:
            join_url = ""
            start_url = "https://zoom.us/s/host"

        class Apt:
            zoom_meeting = Zoom()

        class Case:
            zoom_meeting_url = "https://zoom.us/s/case-host"

        self.assertEqual(_resolve_appointment_zoom_url(Apt(), Case()), "")

        Case.zoom_meeting_url = "https://zoom.us/j/999"
        self.assertEqual(
            _resolve_appointment_zoom_url(Apt(), Case()),
            "https://zoom.us/j/999",
        )

    @patch("apps.scheduling.services.is_zoom_configured", return_value=True)
    @patch("apps.scheduling.services.get_zoom_meeting")
    def test_zoom_meeting_record_is_usable_when_api_lookup_fails(
        self, mock_get, _mock_configured
    ):
        class Zoom:
            zoom_meeting_id = "12345678901"
            join_url = "https://zoom.us/j/12345678901"

        mock_get.side_effect = ZoomAPIError("Meeting does not exist")
        self.assertFalse(_zoom_meeting_record_is_usable(Zoom()))

    @patch("apps.scheduling.services.is_zoom_configured", return_value=True)
    @patch("apps.scheduling.services.get_zoom_meeting")
    def test_zoom_meeting_record_is_usable_when_api_lookup_succeeds(
        self, mock_get, _mock_configured
    ):
        class Zoom:
            zoom_meeting_id = "12345678901"
            join_url = "https://zoom.us/j/12345678901"

        mock_get.return_value = {"join_url": "https://zoom.us/j/12345678901"}
        self.assertTrue(_zoom_meeting_record_is_usable(Zoom()))
