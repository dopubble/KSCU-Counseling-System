from django.test import TestCase

from apps.accounts.models import User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.counseling.services import build_case_session_cards


class TerminationRecordSessionCardTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="term-client@example.com",
            password="pass",
            name="내담자",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.counselor = User.objects.create_user(
            email="term-counselor@example.com",
            password="pass",
            name="상담사",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        application = CounselingApplication.objects.create(
            client=self.client_user,
            status=ApplicationStatus.IN_PROGRESS,
            counseling_types=["개인상담"],
            reason="테스트",
            preferred_schedule={},
        )
        self.case = Case.objects.create(
            application=application,
            client=self.client_user,
            counselor=self.counselor,
            status=CaseStatus.ACTIVE,
            total_sessions=8,
            remaining_sessions=8,
            counseling_method=CounselingMethod.IN_PERSON,
        )

    def test_termination_record_button_only_on_last_session(self):
        cards = build_case_session_cards(self.case)
        self.assertEqual(len(cards), 8)
        self.assertTrue(cards[0].show_counselor_journal)
        self.assertFalse(cards[0].show_termination_record)
        self.assertTrue(cards[-1].show_counselor_journal)
        self.assertTrue(cards[-1].show_termination_record)
        self.assertEqual(cards[-1].termination_record_label, "종결기록지 작성")
