"""회기 카드 상태 배지는 화면 표시만 바꾼다."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.accounts.models import User, UserRole, UserStatus
from apps.counseling.models import Case, CaseStatus, CounselingApplication, CounselingMethod
from apps.counseling.services import (
    CaseSessionCard,
    CounselorSessionCardView,
    build_case_session_cards,
)
from apps.scheduling.models import Appointment, AppointmentStatus

KST = ZoneInfo("Asia/Seoul")


def _at(hour, minute, second=0):
    return datetime(2026, 11, 27, hour, minute, second, tzinfo=KST)


def _card(status_code, *, appointment_status=None, when=None, duration=50, label=""):
    appointment = None
    if appointment_status is not None:
        appointment = SimpleNamespace(
            status=appointment_status,
            scheduled_at=when,
            duration_minutes=duration,
        )
    return CaseSessionCard(
        session_number=1,
        appointment=appointment,
        journal=None,
        zoom_url="",
        status_code=status_code,
        status_label=label or status_code,
    )


class SessionStatusBadgeTests(SimpleTestCase):
    def test_unbooked_and_no_show_have_no_badge(self):
        planned = _card("PLANNED", label="예정")
        no_show = _card(
            "NO_SHOW",
            appointment_status=AppointmentStatus.NO_SHOW,
            when=_at(18, 0),
            label="노쇼",
        )
        self.assertEqual(planned.status_badge_label, "")
        self.assertEqual(no_show.status_badge_label, "")

    def test_confirmed_badge_changes_only_after_the_stored_end(self):
        start = timezone.now().astimezone(KST).replace(microsecond=0) + timedelta(hours=2)
        card = _card(
            "CONFIRMED",
            appointment_status=AppointmentStatus.CONFIRMED,
            when=start,
            duration=50,
            label="예약 확정",
        )
        self.assertEqual(card.status_badge_label, "예약 확정")
        self.assertEqual(card.status_badge_tone, "confirmed")
        self.assertFalse(card.confirmed_schedule_has_ended(now=start + timedelta(minutes=50)))
        self.assertTrue(card.confirmed_schedule_has_ended(now=start + timedelta(minutes=50, seconds=1)))
        ended = _card(
            "CONFIRMED",
            appointment_status=AppointmentStatus.CONFIRMED,
            when=start - timedelta(hours=4),
            duration=50,
            label="예약 확정",
        )
        self.assertEqual(ended.status_badge_label, "예약 종료")
        self.assertEqual(ended.status_badge_tone, "ended")

    def test_completion_and_cancellation_outrank_elapsed_time(self):
        past = _at(18, 0)
        completed = _card(
            "COMPLETED",
            appointment_status=AppointmentStatus.COMPLETED,
            when=past,
            label="상담종료",
        )
        cancelled = _card(
            "CANCELLED",
            appointment_status=AppointmentStatus.CANCELLED,
            when=past,
            label="취소 완료",
        )
        self.assertEqual(completed.status_badge_label, "상담 완료")
        self.assertEqual(completed.status_badge_tone, "completed")
        self.assertEqual(completed.client_status_label, "상담종료")
        self.assertEqual(cancelled.status_badge_label, "예약 취소")
        self.assertEqual(cancelled.status_badge_tone, "cancelled")
        self.assertEqual(cancelled.client_status_label, "취소 완료")

    def test_other_statuses_keep_their_existing_label(self):
        requested = _card("REQUESTED", label="예약 요청중")
        change = _card(
            "CHANGE_REQUESTED",
            appointment_status=AppointmentStatus.CONFIRMED,
            when=_at(18, 0),
            label="일정 변경 요청 중",
        )
        self.assertEqual(requested.status_badge_label, "예약 요청중")
        self.assertEqual(change.status_badge_label, "일정 변경 요청 중")


class SessionStatusBadgeRenderTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="badge-client@example.com",
            password="pass12345",
            name="내담자",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.other_client = User.objects.create_user(
            email="badge-other@example.com",
            password="pass12345",
            name="다른내담자",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.counselor = User.objects.create_user(
            email="badge-counselor@example.com",
            password="pass12345",
            name="상담사",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        profile = self.counselor.counselor_profile
        profile.cohort = 1
        profile.is_approved = True
        profile.save(update_fields=["cohort", "is_approved", "updated_at"])

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

    def _appointment(self, case, number, when, status):
        return Appointment.objects.create(
            case=case,
            client=case.client,
            counselor=self.counselor,
            session_number=number,
            scheduled_at=when,
            duration_minutes=50,
            status=status,
        )

    def _html(self, case):
        cards = build_case_session_cards(case)
        rendered = {}
        for template, counselor in (
            ("client/partials/session_card.html", False),
            ("counselor/partials/session_card.html", True),
        ):
            rendered[template] = "".join(
                render_to_string(
                    template,
                    {
                        "session": CounselorSessionCardView(card) if counselor else card,
                        "case": case,
                    },
                )
                for card in cards
            )
        return cards, rendered

    def test_both_portals_show_the_same_badges_for_remote_and_in_person(self):
        now = timezone.localtime()
        past = now - timedelta(hours=3)
        upcoming = now + timedelta(hours=3)
        remote = self._case(self.client_user, "CASE-BADGE-R", CounselingMethod.REMOTE)
        in_person = self._case(self.other_client, "CASE-BADGE-I", CounselingMethod.IN_PERSON)
        self._appointment(remote, 1, past, AppointmentStatus.CONFIRMED)
        self._appointment(remote, 2, upcoming, AppointmentStatus.CONFIRMED)
        self._appointment(in_person, 1, past - timedelta(minutes=30), AppointmentStatus.COMPLETED)
        cancelled = self._appointment(
            in_person, 2, upcoming - timedelta(minutes=30), AppointmentStatus.CANCELLED
        )
        cancelled.cancel_reason = "내담자 요청"
        cancelled.confirmed_at = now
        cancelled.save(update_fields=["cancel_reason", "confirmed_at"])

        _, remote_pages = self._html(remote)
        for html in remote_pages.values():
            self.assertEqual(html.count("예약 종료"), 1)
            self.assertEqual(html.count("예약 확정"), 1)
            self.assertIn("client-session-status-badge--ended", html)
            self.assertIn("client-session-status-badge--confirmed", html)
            self.assertNotIn(">예정<", html)

        _, in_person_pages = self._html(in_person)
        for html in in_person_pages.values():
            self.assertIn("상담 완료", html)
            self.assertIn("client-session-status-badge--completed", html)
            self.assertIn("예약 취소", html)
            self.assertIn("client-session-status-badge--cancelled", html)
            self.assertNotIn(">노쇼<", html)
            self.assertNotIn(">예정<", html)

        profile = self.counselor.counselor_profile
        profile.cohort = 2
        profile.save(update_fields=["cohort", "updated_at"])
        cards, _ = self._html(remote)
        self.assertEqual(cards[0].status_badge_label, "예약 종료")
        self.assertEqual(cards[1].status_badge_label, "예약 확정")
