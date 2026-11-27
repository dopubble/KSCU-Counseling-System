"""오늘 확정된 예약 카드만 강조한다."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
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
    mark_current_session_cards,
)
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.sessions_app.models import ZoomMeeting

KST = ZoneInfo("Asia/Seoul")


def _at(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=KST)


def _card(number, *, status=None, when=None, closed=False, code="PLANNED"):
    appointment = None
    if status is not None:
        appointment = SimpleNamespace(status=status, scheduled_at=when)
    return CaseSessionCard(
        session_number=number,
        appointment=appointment,
        journal=None,
        zoom_url="",
        status_code=code,
        status_label=code,
        case_closed=closed,
    )


class TodaySessionHighlightTests(SimpleTestCase):
    def _marked(self, cards, now):
        return mark_current_session_cards(cards, now=now)

    def test_confirmed_appointment_on_today_is_highlighted_all_day(self):
        today = _card(
            2,
            status=AppointmentStatus.CONFIRMED,
            when=_at(2026, 11, 27, 9, 0),
            code="CONFIRMED",
        )
        morning = self._marked([today], _at(2026, 11, 27, 0, 1))
        evening = self._marked([today], _at(2026, 11, 27, 23, 59))
        self.assertTrue(morning[0].is_current_session)
        self.assertTrue(evening[0].is_current_session)

    def test_other_dates_and_midnight_are_not_highlighted(self):
        yesterday = _card(1, status=AppointmentStatus.CONFIRMED, when=_at(2026, 11, 26, 20, 0))
        tomorrow = _card(2, status=AppointmentStatus.CONFIRMED, when=_at(2026, 11, 28, 9, 0))
        marked = self._marked([yesterday, tomorrow], _at(2026, 11, 27, 15, 0))
        self.assertFalse(any(card.is_current_session for card in marked))

        same_day = _card(1, status=AppointmentStatus.CONFIRMED, when=_at(2026, 11, 27, 23, 0))
        self.assertFalse(
            self._marked([same_day], _at(2026, 11, 28, 0, 0))[0].is_current_session
        )

    def test_seoul_date_is_used_when_utc_date_differs(self):
        appointment = _card(
            1,
            status=AppointmentStatus.CONFIRMED,
            when=_at(2026, 11, 27, 0, 30),
        )
        self.assertEqual(
            appointment.appointment.scheduled_at.astimezone(ZoneInfo("UTC")).date().isoformat(),
            "2026-11-26",
        )
        marked = self._marked([appointment], _at(2026, 11, 27, 10, 0))
        self.assertTrue(marked[0].is_current_session)

    def test_cancel_and_reschedule_remove_the_highlight(self):
        confirmed = _card(1, status=AppointmentStatus.CONFIRMED, when=_at(2026, 11, 27, 20, 0))
        cancelled = _card(1, status=AppointmentStatus.CANCELLED, when=_at(2026, 11, 27, 20, 0))
        moved = _card(1, status=AppointmentStatus.CONFIRMED, when=_at(2026, 11, 28, 20, 0))
        now = _at(2026, 11, 27, 21, 0)
        self.assertTrue(self._marked([confirmed], now)[0].is_current_session)
        self.assertFalse(self._marked([cancelled], now)[0].is_current_session)
        self.assertFalse(self._marked([moved], now)[0].is_current_session)

    def test_prior_session_state_does_not_block_todays_appointment(self):
        unfinished = _card(1, status=AppointmentStatus.CONFIRMED, when=_at(2026, 11, 20, 20, 0), code="CONFIRMED")
        today = _card(2, status=AppointmentStatus.CONFIRMED, when=_at(2026, 11, 27, 20, 0), code="CONFIRMED")
        marked = self._marked([unfinished, today], _at(2026, 11, 27, 22, 0))
        self.assertEqual(
            [card.session_number for card in marked if card.is_current_session],
            [2],
        )

    def test_multiple_confirmed_appointments_on_today_are_all_highlighted(self):
        first = _card(1, status=AppointmentStatus.CONFIRMED, when=_at(2026, 11, 27, 10, 0))
        second = _card(2, status=AppointmentStatus.CONFIRMED, when=_at(2026, 11, 27, 20, 0))
        marked = self._marked([first, second], _at(2026, 11, 27, 8, 0))
        self.assertEqual([card.session_number for card in marked if card.is_current_session], [1, 2])

    def test_closed_case_is_not_highlighted(self):
        today = _card(
            1,
            status=AppointmentStatus.CONFIRMED,
            when=_at(2026, 11, 27, 20, 0),
            closed=True,
        )
        marked = self._marked([today], _at(2026, 11, 27, 20, 0))
        self.assertFalse(marked[0].is_current_session)

    def test_clients_are_highlighted_independently(self):
        client_a = [_card(1, status=AppointmentStatus.CONFIRMED, when=_at(2026, 11, 27, 10, 0))]
        client_b = [_card(1, status=AppointmentStatus.CONFIRMED, when=_at(2026, 11, 28, 10, 0))]
        now = _at(2026, 11, 27, 12, 0)
        self.assertTrue(self._marked(client_a, now)[0].is_current_session)
        self.assertFalse(self._marked(client_b, now)[0].is_current_session)


class TodaySessionCardRenderTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="today-session-client@example.com",
            password="pass12345",
            name="내담자",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.other_client = User.objects.create_user(
            email="today-session-other@example.com",
            password="pass12345",
            name="다른내담자",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.counselor = User.objects.create_user(
            email="today-session-counselor@example.com",
            password="pass12345",
            name="상담사",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        profile = self.counselor.counselor_profile
        profile.cohort = 1
        profile.is_approved = True
        profile.save(update_fields=["cohort", "is_approved", "updated_at"])
        self.today = timezone.localtime().replace(hour=20, minute=0, second=0, microsecond=0)
        self.yesterday = self.today - timedelta(days=1)
        self.tomorrow = self.today + timedelta(days=1)

    def _case(self, client, number, method):
        application = CounselingApplication.objects.create(
            client=client,
            counseling_types=["진로상담"],
            reason="상담",
            counseling_method=method,
        )
        return Case.objects.create(
            application=application,
            client=client,
            counselor=self.counselor,
            case_number=number,
            status=CaseStatus.ACTIVE,
            counseling_method=method,
            total_sessions=2,
            remaining_sessions=2,
        )

    def _appointment(self, case, session_number, when, status, meeting_id=""):
        appointment = Appointment.objects.create(
            case=case,
            client=case.client,
            counselor=self.counselor,
            session_number=session_number,
            scheduled_at=when,
            duration_minutes=50,
            status=status,
        )
        if meeting_id:
            ZoomMeeting.objects.create(
                appointment=appointment,
                zoom_meeting_id=meeting_id,
                join_url=f"https://zoom.us/j/{meeting_id}",
                start_url=f"https://zoom.us/s/{meeting_id}",
            )
        return appointment

    def _rendered(self, case):
        cards = build_case_session_cards(case)
        pages = {}
        for template, counselor in (
            ("client/partials/session_card.html", False),
            ("counselor/partials/session_card.html", True),
        ):
            pages[template] = "".join(
                render_to_string(
                    template,
                    {
                        "session": CounselorSessionCardView(card) if counselor else card,
                        "case": case,
                    },
                )
                for card in cards
            )
        return cards, pages

    def test_counselor_and_client_highlight_only_todays_confirmed_session(self):
        remote = self._case(self.client_user, "CASE-TODAY-R", CounselingMethod.REMOTE)
        in_person = self._case(self.other_client, "CASE-TODAY-I", CounselingMethod.IN_PERSON)
        self._appointment(
            remote, 1, self.yesterday, AppointmentStatus.CONFIRMED, meeting_id="111"
        )
        self._appointment(
            remote, 2, self.today, AppointmentStatus.CONFIRMED, meeting_id="222"
        )
        self._appointment(in_person, 1, self.yesterday.replace(hour=18), AppointmentStatus.CONFIRMED)
        self._appointment(in_person, 2, self.today.replace(hour=18), AppointmentStatus.CONFIRMED)

        cards, pages = self._rendered(remote)
        self.assertFalse(cards[0].is_current_session)
        self.assertTrue(cards[1].is_current_session)
        for html in pages.values():
            self.assertEqual(html.count("오늘 상담"), 1)
            self.assertEqual(html.count("client-session-card--current"), 1)
            self.assertNotIn("현재 회기", html)
            today_at = html.index('id="session-2"')
            article = html.rfind("<article", 0, today_at)
            self.assertIn("오늘 상담", html[article:article + 500])

        in_person_cards, _ = self._rendered(in_person)
        self.assertTrue(in_person_cards[1].is_current_session)
        self.assertFalse(in_person_cards[0].is_current_session)

        profile = self.counselor.counselor_profile
        profile.cohort = 2
        profile.save(update_fields=["cohort", "updated_at"])
        again, _ = self._rendered(remote)
        self.assertTrue(again[1].is_current_session)

    def test_cancel_reschedule_and_closure_clear_the_highlight(self):
        case = self._case(self.client_user, "CASE-TODAY-MOVE", CounselingMethod.REMOTE)
        appointment = self._appointment(
            case, 1, self.today, AppointmentStatus.CONFIRMED, meeting_id="333"
        )
        self._appointment(
            case, 2, self.tomorrow, AppointmentStatus.CONFIRMED, meeting_id="444"
        )
        cards, _ = self._rendered(case)
        self.assertTrue(cards[0].is_current_session)
        self.assertFalse(cards[1].is_current_session)

        appointment.scheduled_at = self.tomorrow.replace(hour=15)
        appointment.save(update_fields=["scheduled_at"])
        cards, pages = self._rendered(case)
        self.assertFalse(any(card.is_current_session for card in cards))
        self.assertNotIn("오늘 상담", pages["client/partials/session_card.html"])

        appointment.scheduled_at = self.today
        appointment.status = AppointmentStatus.CANCELLED
        appointment.save(update_fields=["scheduled_at", "status"])
        cards, _ = self._rendered(case)
        self.assertFalse(any(card.is_current_session for card in cards))

        appointment.status = AppointmentStatus.CONFIRMED
        appointment.save(update_fields=["status"])
        case.status = CaseStatus.CLOSED
        case.save(update_fields=["status"])
        cards, pages = self._rendered(case)
        self.assertFalse(any(card.is_current_session for card in cards))
        self.assertNotIn("오늘 상담", pages["counselor/partials/session_card.html"])
