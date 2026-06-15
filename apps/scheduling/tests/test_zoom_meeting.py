from django.test import SimpleTestCase

from apps.counseling.services import _is_zoom_host_url, _resolve_appointment_zoom_url
from apps.scheduling.utils import (
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
