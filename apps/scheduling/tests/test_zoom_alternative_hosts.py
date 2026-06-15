from django.test import SimpleTestCase

from apps.scheduling.utils import (
    _zoom_meeting_settings,
    counselor_zoom_host_email,
    pick_meeting_launch_url,
)


class ZoomAlternativeHostTests(SimpleTestCase):
    def test_counselor_zoom_host_email(self):
        class User:
            email = "  counselor@example.com  "

        self.assertEqual(counselor_zoom_host_email(User()), "counselor@example.com")
        self.assertEqual(counselor_zoom_host_email(None), "")

    def test_zoom_meeting_settings_includes_alternative_host(self):
        settings = _zoom_meeting_settings(alternative_host_email="c@example.com")
        self.assertEqual(settings["alternative_hosts"], "c@example.com")
        self.assertFalse(settings["alternative_hosts_email_notification"])
        self.assertTrue(settings["join_before_host"])

    def test_pick_meeting_launch_url_prefers_join(self):
        url = pick_meeting_launch_url(
            {
                "join_url": "https://zoom.us/j/123",
                "start_url": "https://zoom.us/s/456",
            }
        )
        self.assertEqual(url, "https://zoom.us/j/123")
