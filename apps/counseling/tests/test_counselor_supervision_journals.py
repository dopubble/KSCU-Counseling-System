"""상담사별 수퍼비전 일지 조회 — 완료 일지 QuerySet과 담당 기수 권한."""

from django.test import TestCase

from apps.accounts.models import SupervisorProfile, User, UserRole, UserStatus
from apps.counseling.cohort_journal_service import get_counselor_journals_for_supervision
from apps.counseling.journal_permissions import user_can_browse_counselor_journals
from apps.counseling.tests.test_journal_permissions import (
    _create_case,
    _create_client,
    _create_counselor,
    _create_journal,
)
from apps.sessions_app.models import CounselingJournal


def _supervisor(email: str, cohorts: list[int]) -> User:
    user = User.objects.create_user(
        email=email,
        password="pass12345",
        name="수퍼바이저",
        role=UserRole.SUPERVISOR,
        status=UserStatus.ACTIVE,
    )
    SupervisorProfile.objects.update_or_create(
        user=user,
        defaults={"assigned_cohorts": cohorts},
    )
    return user


class CounselorSupervisionJournalTests(TestCase):
    def setUp(self):
        self.counselor = _create_counselor("수련자김", cohort=1)
        self.peer = _create_counselor("수련자이", cohort=1)
        self.other = _create_counselor("수련자박", cohort=2)
        self.client_a = _create_client("내담자홍")
        self.client_b = _create_client("내담자최")
        self.case = _create_case(self.counselor, self.client_a, "CASE-CSJ-1")
        self.peer_case = _create_case(self.peer, self.client_b, "CASE-CSJ-2")
        self.other_case = _create_case(self.other, _create_client("내담자정"), "CASE-CSJ-3")
        self.journal_s2 = _create_journal(self.case, self.counselor, session_number=2)
        self.journal_s1 = _create_journal(self.case, self.counselor, session_number=1)
        self.draft = CounselingJournal.objects.create(
            case=self.case,
            counselor=self.counselor,
            session_number=3,
            is_draft=True,
        )
        self.peer_journal = _create_journal(self.peer_case, self.peer, session_number=1)
        self.other_journal = _create_journal(self.other_case, self.other, session_number=1)
        self.supervisor = _supervisor("sv-cohort1@example.com", [1])

    def test_assigned_cohort_returns_only_completed_journals_of_that_counselor(self):
        self.assertTrue(user_can_browse_counselor_journals(self.supervisor, self.counselor))
        journals = list(get_counselor_journals_for_supervision(self.counselor))
        self.assertEqual(journals, [self.journal_s1, self.journal_s2])
        self.assertNotIn(self.draft, journals)
        self.assertNotIn(self.peer_journal, journals)
        self.assertTrue(all(journal.counselor_id == self.counselor.pk for journal in journals))
        self.assertTrue(all(journal.is_draft is False for journal in journals))

    def test_other_cohort_supervisor_is_denied(self):
        self.assertFalse(user_can_browse_counselor_journals(self.supervisor, self.other))

    def test_admin_and_superuser_can_browse_any_cohort(self):
        admin = User.objects.create_user(
            email="admin-csj@example.com",
            password="pass12345",
            name="관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        superuser = User.objects.create_superuser(
            email="super-csj@example.com",
            password="pass12345",
            name="최고관리자",
        )
        self.assertTrue(user_can_browse_counselor_journals(admin, self.other))
        self.assertTrue(user_can_browse_counselor_journals(superuser, self.counselor))
        self.assertEqual(
            list(get_counselor_journals_for_supervision(self.other)),
            [self.other_journal],
        )

    def test_counselor_cannot_browse_supervision_journals(self):
        self.assertFalse(
            user_can_browse_counselor_journals(self.counselor, self.peer)
        )
        self.assertFalse(
            user_can_browse_counselor_journals(self.peer, self.counselor)
        )

    def test_journals_are_ordered_by_session_number(self):
        third_case = _create_case(self.counselor, self.client_b, "CASE-CSJ-4")
        later = _create_journal(third_case, self.counselor, session_number=1)
        journals = list(get_counselor_journals_for_supervision(self.counselor))
        self.assertEqual(
            [journal.session_number for journal in journals],
            [1, 1, 2],
        )
        self.assertEqual(journals[0].case.case_number, "CASE-CSJ-1")
        self.assertEqual(journals[1], later)

    def test_related_rows_do_not_add_queries(self):
        with self.assertNumQueries(1):
            journals = list(get_counselor_journals_for_supervision(self.counselor))
            for journal in journals:
                journal.case.client.name
                journal.counselor.counselor_profile.cohort
                journal.appointment
