"""duplicate_zoom_host_fix — 클러스터 탐지·재배정 수집 테스트."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone
from zoneinfo import ZoneInfo

from apps.scheduling.duplicate_zoom_host_fix import (
    collect_duplicate_reassignments,
    find_same_host_overlap_clusters,
)

KST = ZoneInfo("Asia/Seoul")
HOST1 = "host1@example.com"
HOST2 = "host2@example.com"


def _make_apt(pk: int, start: datetime, host_email: str = HOST1):
    zoom = MagicMock()
    zoom.zoom_host_email = host_email
    zoom.zoom_meeting_id = f"mid-{pk}"
    zoom.join_url = f"https://zoom.us/j/{100000 + pk}"

    apt = MagicMock()
    apt.pk = pk
    apt.scheduled_at = start
    apt.duration_minutes = 50
    apt.client.name = f"client-{pk}"
    apt.zoom_meeting = zoom
    return apt


class DuplicateZoomHostFixTests(TestCase):
    @patch(
        "apps.scheduling.duplicate_zoom_host_fix.get_zoom_licensed_user_emails",
        return_value=(HOST1, HOST2),
    )
    def test_finds_overlap_cluster_same_host(self, _mock_licensed):
        base = timezone.make_aware(datetime(2026, 7, 8, 10, 0), KST)
        a1 = _make_apt(1, base, HOST1)
        a2 = _make_apt(2, base, HOST1)
        clusters = find_same_host_overlap_clusters([a1, a2])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]), 2)

    @patch(
        "apps.scheduling.duplicate_zoom_host_fix.get_zoom_licensed_user_emails",
        return_value=(HOST1, HOST2),
    )
    def test_no_cluster_when_different_hosts(self, _mock_licensed):
        base = timezone.make_aware(datetime(2026, 7, 8, 10, 0), KST)
        a1 = _make_apt(1, base, HOST1)
        a2 = _make_apt(2, base, HOST2)
        clusters = find_same_host_overlap_clusters([a1, a2])
        self.assertEqual(clusters, [])

    @patch(
        "apps.scheduling.duplicate_zoom_host_fix.get_zoom_licensed_user_emails",
        return_value=(HOST1, HOST2),
    )
    def test_no_cluster_when_separated_by_buffer(self, _mock_licensed):
        base = timezone.make_aware(datetime(2026, 7, 8, 10, 0), KST)
        a1 = _make_apt(1, base, HOST1)
        # 10:00 + 50min + 30min buffer = 11:20; next at 11:30 OK
        a2 = _make_apt(2, base + timedelta(hours=1, minutes=30), HOST1)
        clusters = find_same_host_overlap_clusters([a1, a2])
        self.assertEqual(clusters, [])

    @patch(
        "apps.scheduling.duplicate_zoom_host_fix.assign_host_emails_for_appointments",
        return_value={"1": HOST1, "2": HOST2},
    )
    @patch(
        "apps.scheduling.duplicate_zoom_host_fix.get_zoom_licensed_user_emails",
        return_value=(HOST1, HOST2),
    )
    def test_collect_reassignments_second_in_cluster(self, _mock_lic, _mock_assign):
        base = timezone.make_aware(datetime(2026, 7, 8, 10, 0), KST)
        a1 = _make_apt(1, base, HOST1)
        a2 = _make_apt(2, base, HOST1)
        fixes = collect_duplicate_reassignments([a1, a2])
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0][0].pk, 2)
        self.assertEqual(fixes[0][2], HOST2)

    @patch(
        "apps.scheduling.duplicate_zoom_host_fix.reassign_appointment_zoom_host",
        return_value="[fixed]",
    )
    @patch(
        "apps.scheduling.duplicate_zoom_host_fix.assign_host_emails_for_appointments",
        return_value={"1": HOST1, "2": HOST2},
    )
    @patch(
        "apps.scheduling.duplicate_zoom_host_fix.confirmed_remote_appointments_queryset",
    )
    @patch(
        "apps.scheduling.duplicate_zoom_host_fix.is_zoom_configured",
        return_value=True,
    )
    def test_rebalance_after_confirm_fixes_stored_host_mismatch(
        self,
        _mock_configured,
        mock_qs,
        _mock_assign,
        mock_reassign,
    ):
        from apps.scheduling.duplicate_zoom_host_fix import (
            rebalance_zoom_hosts_after_confirm,
        )

        base = timezone.make_aware(datetime(2026, 7, 8, 11, 0), KST)
        a1 = _make_apt(1, base, HOST1)
        a2 = _make_apt(2, base + timedelta(minutes=30), HOST1)
        mock_qs.return_value = [a1, a2]

        messages = rebalance_zoom_hosts_after_confirm(a2)
        self.assertEqual(messages, ["[fixed]"])
        mock_reassign.assert_called_once_with(
            a2, HOST2, dry_run=False, notify_link_change=False
        )

    @patch(
        "apps.scheduling.duplicate_zoom_host_fix.reassign_appointment_zoom_host",
        return_value="[fixed]",
    )
    @patch(
        "apps.scheduling.duplicate_zoom_host_fix.assign_host_emails_for_appointments",
        return_value={"1": HOST2, "2": HOST2},
    )
    @patch(
        "apps.scheduling.duplicate_zoom_host_fix.confirmed_remote_appointments_queryset",
    )
    @patch(
        "apps.scheduling.duplicate_zoom_host_fix.is_zoom_configured",
        return_value=True,
    )
    def test_rebalance_after_confirm_fixes_same_day_stored_mismatch(
        self,
        _mock_configured,
        mock_qs,
        _mock_assign,
        mock_reassign,
    ):
        from apps.scheduling.duplicate_zoom_host_fix import (
            rebalance_zoom_hosts_after_confirm,
        )

        day = timezone.make_aware(datetime(2026, 7, 8, 10, 0), KST)
        other_day = timezone.make_aware(datetime(2026, 7, 9, 10, 0), KST)
        a1 = _make_apt(1, day, HOST1)
        a2 = _make_apt(2, day + timedelta(hours=2), HOST2)
        a3 = _make_apt(3, other_day, HOST1)
        mock_qs.return_value = [a1, a2, a3]

        rebalance_zoom_hosts_after_confirm(a2)
        mock_reassign.assert_called_once_with(
            a1, HOST2, dry_run=False, notify_link_change=False
        )
