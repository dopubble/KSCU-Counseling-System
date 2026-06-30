"""Zoom Licensed 호스트 배정 테스트."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.reports.appointment_calendar import assign_zoom_hosts, CalendarInterval
from apps.scheduling.zoom_hosts import (
    assign_host_emails_for_appointments,
    email_for_host_id,
    get_zoom_host_pool,
    host_id_for_email,
)


class ZoomHostAssignmentTests(TestCase):
    @override_settings(
        ZOOM_LICENSED_USERS="sscukscu@gmail.com,sedulife@mail.kcu.ac",
    )
    def test_host_pool_matches_licensed_users(self):
        self.assertEqual(get_zoom_host_pool(), ("host_01", "host_02"))
        self.assertEqual(email_for_host_id("host_01"), "sscukscu@gmail.com")
        self.assertEqual(email_for_host_id("host_02"), "sedulife@mail.kcu.ac")
        self.assertEqual(host_id_for_email("sedulife@mail.kcu.ac"), "host_02")

    @override_settings(
        ZOOM_LICENSED_USERS="sscukscu@gmail.com,sedulife@mail.kcu.ac",
    )
    def test_overlapping_intervals_use_different_host_emails(self):
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
        host_ids = assign_zoom_hosts(intervals)
        emails = {
            appointment_id: email_for_host_id(host_id)
            for appointment_id, host_id in host_ids.items()
        }
        self.assertEqual(emails["a"], "sscukscu@gmail.com")
        self.assertEqual(emails["b"], "sedulife@mail.kcu.ac")

    @override_settings(
        ZOOM_LICENSED_USERS="sscukscu@gmail.com,sedulife@mail.kcu.ac",
        ZOOM_HOST_BUFFER_MINUTES=30,
    )
    def test_consecutive_hourly_slots_use_different_hosts(self):
        start = timezone.now().replace(hour=14, minute=0, second=0, microsecond=0)
        intervals = [
            CalendarInterval("a", start, start + timedelta(minutes=50), True),
            CalendarInterval(
                "b",
                start + timedelta(hours=1),
                start + timedelta(hours=1, minutes=50),
                True,
            ),
        ]
        host_ids = assign_zoom_hosts(intervals)
        self.assertEqual(host_ids["a"], "host_01")
        self.assertEqual(host_ids["b"], "host_02")

    @override_settings(
        ZOOM_LICENSED_USERS="sscukscu@gmail.com,sedulife@mail.kcu.ac",
    )
    def test_assign_host_emails_for_empty_list(self):
        self.assertEqual(assign_host_emails_for_appointments([]), {})
