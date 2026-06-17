from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.counseling.models import CounselingMethod
from apps.reports.appointment_calendar import (
    assign_zoom_hosts,
    build_calendar_events,
    get_mock_calendar_events,
    CalendarInterval,
    zoom_host_label,
)


class AppointmentCalendarTests(TestCase):
    def test_zoom_host_label(self):
        self.assertEqual(zoom_host_label("host_01"), "Zoom 호스트 1번")
        self.assertEqual(zoom_host_label("host_02"), "Zoom 호스트 2번")

    def test_assign_zoom_hosts_splits_overlaps(self):
        start = timezone.now().replace(minute=0, second=0, microsecond=0)
        intervals = [
            CalendarInterval("a", start, start + timedelta(minutes=50), True),
            CalendarInterval(
                "b",
                start + timedelta(minutes=30),
                start + timedelta(minutes=80),
                True,
            ),
        ]
        with override_settings(ZOOM_HOST_POOL="host_01,host_02"):
            assignments = assign_zoom_hosts(intervals)
        self.assertEqual(assignments["a"], "host_01")
        self.assertEqual(assignments["b"], "host_02")

    def test_mock_events_structure(self):
        events = get_mock_calendar_events()
        self.assertGreaterEqual(len(events), 2)
        first = events[0]
        self.assertIn("title", first)
        self.assertIn("extendedProps", first)
        self.assertEqual(first["extendedProps"]["zoom_host_id"], "host_01")

    def test_build_calendar_events_empty(self):
        events = build_calendar_events()
        self.assertEqual(events, [])
