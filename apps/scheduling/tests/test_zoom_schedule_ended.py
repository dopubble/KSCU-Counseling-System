"""종료된 회기의 Zoom 버튼은 회의에 들어가지 않는다."""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, UserRole, UserStatus
from apps.counseling.models import (
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.counseling.services import (
    CaseSessionCard,
    CounselorSessionCardView,
    build_case_session_cards,
    get_client_home_dashboard,
)
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.sessions_app.models import ZoomMeeting
from apps.scheduling.zoom_links import (
    appointment_zoom_schedule_has_ended,
    resolve_appointment_zoom_join_url,
)

KST = ZoneInfo("Asia/Seoul")
ENDED_COPY = (
    "해당 회기의 상담 일정이 종료되었습니다.",
    "현재 진행할 상담 회기의 Zoom 버튼을 확인해 주세요.",
)


def _at(year, month, day, hour, minute, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=KST)


class ZoomScheduleEndedTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="client-zoom-ended@example.com",
            password="pass12345",
            name="홍연서",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.counselor = User.objects.create_user(
            email="counselor-zoom-ended@example.com",
            password="pass12345",
            name="김은영",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        self.application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["진로상담"],
            reason="상담 신청",
            counseling_method=CounselingMethod.REMOTE,
        )
        self.case = Case.objects.create(
            application=self.application,
            client=self.client_user,
            counselor=self.counselor,
            case_number="CASE-2026-0061",
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.REMOTE,
            total_sessions=2,
            remaining_sessions=1,
        )
        self.session_one = self._appointment(
            session_number=1,
            scheduled_at=_at(2026, 11, 19, 21, 0),
            meeting_id="83284877884",
        )
        self.session_two = self._appointment(
            session_number=2,
            scheduled_at=_at(2026, 12, 3, 20, 0),
            meeting_id="84521159655",
        )

    def _appointment(self, *, session_number, scheduled_at, meeting_id, status=AppointmentStatus.CONFIRMED):
        appointment = Appointment.objects.create(
            case=self.case,
            client=self.client_user,
            counselor=self.counselor,
            session_number=session_number,
            scheduled_at=scheduled_at,
            duration_minutes=50,
            status=status,
        )
        ZoomMeeting.objects.create(
            appointment=appointment,
            zoom_meeting_id=meeting_id,
            join_url=f"https://zoom.us/j/{meeting_id}",
            start_url=f"https://zoom.us/s/{meeting_id}-host",
        )
        return appointment

    def _cards(self):
        cards = build_case_session_cards(self.case)
        return {card.session_number: card for card in cards}

    def test_timezone_is_seoul(self):
        self.assertEqual(timezone.get_current_timezone_name(), "Asia/Seoul")

    def test_ended_boundary_uses_scheduled_date_in_kst(self):
        appointment = self.session_two
        appointment.scheduled_at = _at(2026, 11, 26, 20, 0)
        self.assertFalse(
            appointment_zoom_schedule_has_ended(appointment, now=_at(2026, 11, 26, 0, 1))
        )
        self.assertFalse(
            appointment_zoom_schedule_has_ended(appointment, now=_at(2026, 11, 26, 23, 59, 59))
        )
        self.assertTrue(
            appointment_zoom_schedule_has_ended(appointment, now=_at(2026, 11, 27, 0, 0))
        )
        late_seoul = _at(2026, 11, 27, 0, 30)
        same_seoul_day = _at(2026, 11, 27, 10, 0)
        self.assertEqual(late_seoul.astimezone(ZoneInfo("UTC")).date().isoformat(), "2026-11-26")
        self.assertEqual(same_seoul_day.astimezone(ZoneInfo("UTC")).date().isoformat(), "2026-11-27")
        late_seoul_appointment = self.session_one
        late_seoul_appointment.scheduled_at = late_seoul
        self.assertFalse(
            appointment_zoom_schedule_has_ended(late_seoul_appointment, now=same_seoul_day)
        )
        self.assertFalse(
            appointment_zoom_schedule_has_ended(appointment, now=_at(2026, 11, 25, 23, 59))
        )

    def test_session_one_ended_and_session_two_still_opens(self):
        now = _at(2026, 11, 27, 15, 0)
        cards = self._cards()
        ended = cards[1]
        upcoming = cards[2]

        self.assertTrue(appointment_zoom_schedule_has_ended(ended.appointment, now=now))
        self.assertTrue(ended.zoom_schedule_ended)
        self.assertEqual(ended.zoom_launch_url, "")
        self.assertFalse(upcoming.zoom_schedule_ended)
        self.assertEqual(upcoming.zoom_launch_url, "https://zoom.us/j/84521159655")
        self.assertEqual(
            CounselorSessionCardView(upcoming).zoom_launch_url,
            upcoming.zoom_launch_url,
        )
        self.assertEqual(
            CounselorSessionCardView(upcoming).zoom_url,
            "https://zoom.us/j/84521159655",
        )

        client_html = render_to_string(
            "client/partials/session_card.html",
            {"session": ended, "case": self.case},
        )
        counselor_html = render_to_string(
            "counselor/partials/session_card.html",
            {"session": CounselorSessionCardView(ended), "case": self.case},
        )
        for html in (client_html, counselor_html):
            self.assertIn("zoomScheduleEndedModal", html)
            self.assertIn('type="button"', html)
            self.assertNotIn("target=\"_blank\"", html)
            self.assertNotIn("zoom.us", html)
            self.assertNotIn("83284877884", html)

        future_client = render_to_string(
            "client/partials/session_card.html",
            {"session": upcoming, "case": self.case},
        )
        future_counselor = render_to_string(
            "counselor/partials/session_card.html",
            {"session": CounselorSessionCardView(upcoming), "case": self.case},
        )
        for html in (future_client, future_counselor):
            self.assertIn('target="_blank"', html)
            self.assertIn("https://zoom.us/j/84521159655", html)
            self.assertNotIn("83284877884", html)
            self.assertNotIn("zoomScheduleEndedModal", html)

        modal = render_to_string("includes/zoom_schedule_ended_modal.html")
        self.assertIn("상담 일정 종료", modal)
        for sentence in ENDED_COPY:
            self.assertIn(sentence, modal)
        self.assertIn(">확인<", modal)
        self.assertNotIn("zoom.us", modal)

    def test_today_and_future_dates_keep_the_join_link(self):
        cards = self._cards()
        self.assertFalse(cards[2].zoom_schedule_ended)
        self.assertEqual(cards[2].zoom_launch_url, "https://zoom.us/j/84521159655")
        self.assertFalse(
            appointment_zoom_schedule_has_ended(self.session_two, now=_at(2026, 12, 3, 0, 1))
        )
        self.assertFalse(
            appointment_zoom_schedule_has_ended(self.session_two, now=_at(2026, 12, 3, 23, 50))
        )
        self.assertFalse(
            appointment_zoom_schedule_has_ended(self.session_two, now=_at(2026, 12, 2, 21, 0))
        )

    def test_reschedule_uses_the_new_schedule(self):
        self.session_one.scheduled_at = _at(2026, 12, 10, 20, 0)
        self.session_one.save(update_fields=["scheduled_at"])
        cards = self._cards()
        self.assertFalse(cards[1].zoom_schedule_ended)
        self.assertEqual(cards[1].zoom_launch_url, "https://zoom.us/j/83284877884")
        self.assertNotEqual(cards[1].zoom_launch_url, cards[2].zoom_launch_url)

        self.session_one.scheduled_at = _at(2026, 11, 19, 21, 0)
        self.session_one.save(update_fields=["scheduled_at"])
        cards = self._cards()
        self.assertTrue(cards[1].zoom_schedule_ended)
        self.assertFalse(cards[2].zoom_schedule_ended)

    def test_cancelled_and_completed_still_hide_the_button(self):
        self.session_one.status = AppointmentStatus.COMPLETED
        self.session_one.save(update_fields=["status"])
        self.session_two.status = AppointmentStatus.CANCELLED
        self.session_two.save(update_fields=["status"])
        cards = self._cards()
        for card in (cards[1], cards[2]):
            self.assertFalse(card.show_zoom)
            self.assertFalse(card.zoom_schedule_ended)
            html = render_to_string(
                "client/partials/session_card.html",
                {"session": card, "case": self.case},
            )
            self.assertNotIn("Zoom 회의 바로가기", html)
            self.assertNotIn("zoom.us", html)

    def test_email_resolver_still_returns_the_join_url_after_end(self):
        self.assertTrue(
            appointment_zoom_schedule_has_ended(
                self.session_one,
                now=_at(2026, 11, 27, 15, 0),
            )
        )
        self.assertEqual(
            resolve_appointment_zoom_join_url(self.session_one, self.case),
            "https://zoom.us/j/83284877884",
        )

    def test_home_dashboard_omits_ended_url_and_keeps_future_url(self):
        dashboard = get_client_home_dashboard(self.client_user)
        by_time = {item["scheduled_at"]: item for item in dashboard["recent_appointments"]}
        ended = by_time[self.session_one.scheduled_at]
        upcoming = by_time[self.session_two.scheduled_at]
        self.assertTrue(ended["show_zoom"])
        self.assertTrue(ended["zoom_schedule_ended"])
        self.assertEqual(ended["zoom_url"], "")
        self.assertFalse(upcoming["zoom_schedule_ended"])
        self.assertEqual(upcoming["zoom_url"], "https://zoom.us/j/84521159655")

        ended_html = render_to_string(
            "includes/zoom_session_button.html",
            {
                "zoom_schedule_ended": ended["zoom_schedule_ended"],
                "zoom_launch_url": ended["zoom_url"],
                "zoom_label": "Zoom 상담실 바로가기",
            },
        )
        self.assertNotIn("zoom.us", ended_html)
        self.assertIn("zoomScheduleEndedModal", ended_html)

    def _slice_session(self, html: str, number: int) -> str:
        start = html.index(f'id="session-{number}"')
        return html[start:html.index("</article>", start)]

    def test_case_pages_isolate_each_session_button(self):
        http = Client()
        http.force_login(self.client_user)
        client_page = http.get(reverse("client:case_detail", args=[self.case.pk]))
        self.assertEqual(client_page.status_code, 200)
        client_html = client_page.content.decode()
        home_page = http.get(reverse("home"))
        self.assertEqual(home_page.status_code, 200)
        home_html = home_page.content.decode()

        http.force_login(self.counselor)
        counselor_page = http.get(reverse("counselor:case_detail", args=[self.case.pk]))
        self.assertEqual(counselor_page.status_code, 200)
        counselor_html = counselor_page.content.decode()

        for html in (client_html, counselor_html):
            past = self._slice_session(html, 1)
            current = self._slice_session(html, 2)
            self.assertIn("zoomScheduleEndedModal", past)
            self.assertIn('type="button"', past)
            self.assertNotIn("zoom.us", past)
            self.assertNotIn('target="_blank"', past)
            self.assertIn("https://zoom.us/j/84521159655", current)
            self.assertIn('target="_blank"', current)
            self.assertNotIn("83284877884", current)
            self.assertNotIn("zoomScheduleEndedModal", current)
        self.assertEqual(client_html.count('id="zoomScheduleEndedModal"'), 1)
        self.assertEqual(counselor_html.count('id="zoomScheduleEndedModal"'), 1)
        self.assertIn("해당 회기의 상담 일정이 종료되었습니다.", client_html)
        self.assertIn(">확인<", client_html)

        self.assertIn("zoomScheduleEndedModal", home_html)
        self.assertNotIn("83284877884", home_html)
        self.assertIn("https://zoom.us/j/84521159655", home_html)

        self.session_one.status = AppointmentStatus.COMPLETED
        self.session_one.save(update_fields=["status"])
        http.force_login(self.client_user)
        completed = http.get(reverse("client:case_detail", args=[self.case.pk])).content.decode()
        past = self._slice_session(completed, 1)
        self.assertNotIn("Zoom 회의 바로가기", past)
        self.assertNotIn("zoom.us", past)

    def test_cohort_one_and_two_share_the_same_rule(self):
        for cohort in (1, 2):
            profile = self.counselor.counselor_profile
            profile.cohort = cohort
            profile.is_approved = True
            profile.save(update_fields=["cohort", "is_approved", "updated_at"])
            cards = self._cards()
            self.assertTrue(cards[1].zoom_schedule_ended)
            self.assertEqual(cards[2].zoom_launch_url, "https://zoom.us/j/84521159655")
            self.assertIsInstance(cards[1], CaseSessionCard)
