from django.test import SimpleTestCase

from apps.scheduling.utils import (
    _zoom_meeting_settings,
    pick_meeting_launch_url,
)


class ZoomMeetingSettingsTests(SimpleTestCase):
    def test_zoom_meeting_settings_for_participant_join(self):
        settings = _zoom_meeting_settings()
        self.assertTrue(settings["join_before_host"])
        self.assertFalse(settings["waiting_room"])
        self.assertNotIn("alternative_hosts", settings)

    def test_pick_meeting_launch_url_prefers_join(self):
        url = pick_meeting_launch_url(
            {
                "join_url": "https://zoom.us/j/123",
                "start_url": "https://zoom.us/s/456",
            }
        )
        self.assertEqual(url, "https://zoom.us/j/123")
