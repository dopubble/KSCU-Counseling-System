"""종결기록지는 문서로 남기고, 최종 제출에서 사례를 확정한다."""

from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from datetime import timedelta

from django.utils import timezone

from apps.counseling.models import CaseStatus, CaseTerminationType
from apps.counseling.services import (
    case_operation_label,
    finalize_completed_journal,
    submit_case_records,
)
from apps.counseling.tests.test_case_records_submission import (
    _create_case,
    _create_client,
    _create_counselor,
    _create_initial_record,
    _create_journal,
    _create_termination_record,
)
from apps.counseling.tests.test_supervisor_counselor_journal_pages import (
    _create_case as _sv_case,
    _create_client as _sv_client,
    _create_counselor as _sv_counselor,
    _create_journal as _sv_journal,
    _header,
    _row,
    _supervisor,
)
from apps.sessions_app.models import TerminationCounselingRecord


class TerminationRecordDoesNotCloseCaseTests(TestCase):
    def setUp(self):
        self.counselor = _create_counselor("준비상담")
        self.client_user = _create_client("준비내담")
        self.case = _create_case(self.counselor, self.client_user, "CASE-READY-1")
        self.journal = _create_journal(self.case, self.counselor, 1)
        self.initial = _create_initial_record(self.case, self.counselor)
        self.http = HttpClient()
        self.http.force_login(self.counselor)

    def _save_termination(self, *, draft_then_complete=False):
        response = self.http.post(
            reverse("counselor:termination_record_create", kwargs={"pk": self.case.pk}),
            {
                "counseling_period": "2026-03-01",
                "main_topics": "주제",
                "termination_reason": "목표 달성",
                "counselor_opinion": "소견",
                "post_termination_plan": "계획",
                "other_notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        return response

    def test_completed_termination_record_keeps_case_active(self):
        self._save_termination()
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, CaseStatus.ACTIVE)
        self.assertIsNone(self.case.records_submitted_at)
        self.assertEqual(self.case.termination_type, "")
        self.assertFalse(
            TerminationCounselingRecord.objects.get(case=self.case).is_draft
        )

    def test_termination_record_remains_editable_before_submit(self):
        self._save_termination()
        edit = self.http.get(
            reverse("counselor:termination_record_edit", kwargs={"pk": self.case.pk})
        )
        self.assertEqual(edit.status_code, 200)
        posted = self.http.post(
            reverse("counselor:termination_record_edit", kwargs={"pk": self.case.pk}),
            {
                "counseling_period": "2026-03-02",
                "main_topics": "수정 주제",
                "termination_reason": "수정 사유",
                "counselor_opinion": "",
                "post_termination_plan": "",
                "other_notes": "",
            },
        )
        self.assertEqual(posted.status_code, 302)
        record = TerminationCounselingRecord.objects.get(case=self.case)
        self.assertEqual(record.main_topics, "수정 주제")
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, CaseStatus.ACTIVE)
        self.assertIsNone(self.case.records_submitted_at)

    def test_journal_and_initial_record_remain_editable_before_submit(self):
        self._save_termination()
        journal = self.http.get(
            reverse(
                "counselor:journal_edit",
                kwargs={"pk": self.case.pk, "session_number": 1},
            )
        )
        initial = self.http.get(
            reverse("counselor:initial_record_edit", kwargs={"pk": self.case.pk})
        )
        self.assertEqual(journal.status_code, 200)
        self.assertEqual(initial.status_code, 200)


class NormalFinalSubmissionTests(TestCase):
    def setUp(self):
        self.counselor = _create_counselor("정상제출상담")
        self.client_user = _create_client("정상제출내담")
        self.case = _create_case(self.counselor, self.client_user, "CASE-FINAL-1")
        self.journal = _create_journal(self.case, self.counselor, 1)
        self.initial = _create_initial_record(self.case, self.counselor)
        _create_termination_record(self.case, self.counselor)
        self.http = HttpClient()
        self.http.force_login(self.counselor)
        self.submit_url = reverse(
            "counselor:case_records_submit", kwargs={"pk": self.case.pk}
        )

    def test_active_submit_closes_as_normal(self):
        response = self.http.post(self.submit_url, {"confirm": "1"}, follow=True)

        self.case.refresh_from_db()
        self.assertEqual(self.case.status, CaseStatus.CLOSED)
        self.assertEqual(self.case.termination_type, CaseTerminationType.NORMAL)
        self.assertIsNotNone(self.case.records_submitted_at)
        self.assertIsNotNone(self.case.closed_at)
        self.assertContains(response, "최종 제출되었습니다.")

    def test_records_are_locked_after_submit(self):
        self.http.post(self.submit_url, {"confirm": "1"})

        self.assertEqual(
            self.http.get(
                reverse("counselor:termination_record_edit", kwargs={"pk": self.case.pk})
            ).status_code,
            403,
        )
        self.assertEqual(
            self.http.get(
                reverse(
                    "counselor:journal_edit",
                    kwargs={"pk": self.case.pk, "session_number": 1},
                )
            ).status_code,
            403,
        )
        self.assertEqual(
            self.http.get(
                reverse("counselor:initial_record_edit", kwargs={"pk": self.case.pk})
            ).status_code,
            403,
        )

    def test_missing_or_draft_termination_record_blocks_submit(self):
        TerminationCounselingRecord.objects.filter(case=self.case).delete()
        missing = self.http.post(self.submit_url, {"confirm": "1"}, follow=True)
        self.assertIn(
            "종결기록지가 작성되지 않았습니다.",
            missing.context["records_submit_errors"],
        )
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, CaseStatus.ACTIVE)

        TerminationCounselingRecord.objects.create(
            case=self.case,
            counselor=self.counselor,
            is_draft=True,
        )
        draft = self.http.post(self.submit_url, {"confirm": "1"}, follow=True)
        self.assertIn(
            "종결기록지가 작성되지 않았습니다.",
            draft.context["records_submit_errors"],
        )
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, CaseStatus.ACTIVE)
        self.assertEqual(self.case.termination_type, "")

    def test_hold_and_transferred_cases_cannot_submit(self):
        for status in (CaseStatus.ON_HOLD, CaseStatus.TRANSFERRED):
            self.case.status = status
            self.case.records_submitted_at = None
            self.case.save(update_fields=["status", "records_submitted_at"])
            response = self.http.post(self.submit_url, {"confirm": "1"}, follow=True)
            self.assertIn(
                "휴지 또는 이관 상태의 사례는 최종 제출할 수 없습니다.",
                response.context["records_submit_errors"],
            )
            self.case.refresh_from_db()
            self.assertEqual(self.case.status, status)
            self.assertIsNone(self.case.records_submitted_at)


class LastSessionStaysActiveTests(TestCase):
    def test_last_completed_session_reaches_zero_without_closing(self):
        counselor = _sv_counselor("막회기상담", cohort=1)
        client = _sv_client("막회기내담")
        case = _create_case(
            counselor, client, "CASE-LAST-1", total=1, remaining=1
        )
        finalize_completed_journal(_create_journal(case, counselor, 1))
        case.refresh_from_db()

        self.assertEqual(case.remaining_sessions, 0)
        self.assertEqual(case.status, CaseStatus.ACTIVE)
        self.assertEqual(case.termination_type, "")
        self.assertIsNone(case.records_submitted_at)
        self.assertIsNone(case.closed_at)
        self.assertEqual(case_operation_label(case), "종결준비")

        counselor_http = HttpClient()
        counselor_http.force_login(counselor)
        dashboard = counselor_http.get(reverse("counselor:dashboard"))
        detail = counselor_http.get(
            reverse("counselor:case_detail", kwargs={"pk": case.pk})
        )
        self.assertContains(dashboard, "종결준비")
        self.assertContains(dashboard, "사례기록 최종 제출")
        self.assertContains(detail, "종결준비")

        supervisor = _supervisor("sv-last@example.com", "막회기수퍼", [1])
        supervisor_http = HttpClient()
        supervisor_http.force_login(supervisor)
        page = supervisor_http.get(
            reverse(
                "supervisor:counselor_journals",
                kwargs={"counselor_id": counselor.pk},
            )
        )
        body = page.content.decode()
        self.assertIn(">종결준비</span>", _header(body, case.pk))
        self.assertIn("종결 전", _row(body, case.pk, "termination"))

        blocked = counselor_http.post(
            reverse("counselor:case_records_submit", kwargs={"pk": case.pk}),
            {"confirm": "1"},
            follow=True,
        )
        self.assertIn(
            "종결기록지가 작성되지 않았습니다.",
            blocked.context["records_submit_errors"],
        )
        case.refresh_from_db()
        self.assertEqual(case.status, CaseStatus.ACTIVE)


class ReadyLabelTests(TestCase):
    def setUp(self):
        self.counselor = _create_counselor("회기준비상담")
        self.client_user = _create_client("회기준비내담")
        self.case = _create_case(self.counselor, self.client_user, "CASE-READY-SESS")
        self.http = HttpClient()
        self.http.force_login(self.counselor)

    def test_active_label_follows_termination_record_then_remaining_sessions(self):
        self.assertEqual(case_operation_label(self.case), "진행중")

        self.case.remaining_sessions = 0
        self.case.save(update_fields=["remaining_sessions"])
        self.case.refresh_from_db()
        self.assertEqual(case_operation_label(self.case), "종결준비")

        record = _create_termination_record(self.case, self.counselor)
        self.case.refresh_from_db()
        self.assertEqual(case_operation_label(self.case), "종결준비")

        self.case.remaining_sessions = 2
        self.case.save(update_fields=["remaining_sessions"])
        self.case.refresh_from_db()
        self.assertEqual(case_operation_label(self.case), "종결준비")

        record.delete()
        self.case.refresh_from_db()
        self.assertEqual(case_operation_label(self.case), "진행중")

    def test_zero_remaining_without_record_is_ready_but_submit_fails(self):
        self.case.remaining_sessions = 0
        self.case.save(update_fields=["remaining_sessions"])
        _create_journal(self.case, self.counselor, 1)

        dashboard = self.http.get(reverse("counselor:dashboard"))
        detail = self.http.get(
            reverse("counselor:case_detail", kwargs={"pk": self.case.pk})
        )
        self.assertContains(dashboard, "종결준비")
        self.assertContains(detail, "종결준비")

        supervisor = _supervisor("sv-zero@example.com", "회기수퍼", [1])
        supervisor_http = HttpClient()
        supervisor_http.force_login(supervisor)
        page = supervisor_http.get(
            reverse(
                "supervisor:counselor_journals",
                kwargs={"counselor_id": self.counselor.pk},
            )
        )
        body = page.content.decode()
        self.assertIn(">종결준비</span>", _header(body, self.case.pk))
        self.assertIn("종결 전", _row(body, self.case.pk, "termination"))

        submitted = self.http.post(
            reverse("counselor:case_records_submit", kwargs={"pk": self.case.pk}),
            {"confirm": "1"},
            follow=True,
        )
        self.assertIn(
            "종결기록지가 작성되지 않았습니다.",
            submitted.context["records_submit_errors"],
        )
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, CaseStatus.ACTIVE)
        self.assertEqual(self.case.termination_type, "")
        self.assertIsNone(self.case.records_submitted_at)
        self.assertEqual(case_operation_label(self.case), "종결준비")

    def test_ready_with_completed_record_submits_as_normal(self):
        self.case.remaining_sessions = 0
        self.case.save(update_fields=["remaining_sessions"])
        _create_termination_record(self.case, self.counselor)
        self.case.refresh_from_db()
        self.assertEqual(case_operation_label(self.case), "종결준비")

        response = self.http.post(
            reverse("counselor:case_records_submit", kwargs={"pk": self.case.pk}),
            {"confirm": "1"},
            follow=True,
        )
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, CaseStatus.CLOSED)
        self.assertEqual(self.case.termination_type, CaseTerminationType.NORMAL)
        self.assertIsNotNone(self.case.records_submitted_at)
        self.assertEqual(case_operation_label(self.case), "정상종결")
        self.assertContains(response, "정상종결")


class ReadyAndEarlySubmissionTests(TestCase):
    def test_supervisor_and_counselor_show_ready_label(self):
        counselor = _sv_counselor("준비표시", cohort=1)
        client = _sv_client("준비표시내담")
        case = _sv_case(counselor, client, "CASE-READY-LABEL")
        _sv_journal(case, counselor, session_number=1)
        _create_termination_record(case, counselor)
        supervisor = _supervisor("sv-ready@example.com", "준비수퍼", [1])
        http = HttpClient()
        http.force_login(supervisor)
        page = http.get(
            reverse("supervisor:counselor_journals", kwargs={"counselor_id": counselor.pk})
        )
        body = page.content.decode()
        self.assertIn(">종결준비</span>", _header(body, case.pk))
        row = _row(body, case.pk, "termination")
        self.assertIn("작성완료", row)

        counselor_http = HttpClient()
        counselor_http.force_login(counselor)
        dashboard = counselor_http.get(reverse("counselor:dashboard"))
        self.assertContains(dashboard, "종결준비")
        self.assertContains(dashboard, "사례기록 최종 제출")

    def test_early_case_submit_keeps_early_type_and_closed_at(self):
        counselor = _create_counselor("조기제출상담")
        client = _create_client("조기제출내담")
        case = _create_case(
            counselor,
            client,
            "CASE-EARLY-SUB",
            status=CaseStatus.CLOSED,
        )
        closed_at = timezone.now() - timedelta(days=2)
        case.termination_type = CaseTerminationType.EARLY
        case.closed_at = closed_at
        case.save(update_fields=["termination_type", "closed_at"])
        _create_journal(case, counselor, 1)
        http = HttpClient()
        http.force_login(counselor)

        before = http.get(reverse("counselor:dashboard"))
        self.assertContains(before, "조기종결")
        self.assertContains(
            before,
            reverse("counselor:case_records_submit", kwargs={"pk": case.pk}),
        )
        self.assertEqual(
            http.get(
                reverse("counselor:termination_record_create", kwargs={"pk": case.pk})
            ).status_code,
            200,
        )
        _create_termination_record(case, counselor)
        self.assertEqual(
            http.get(
                reverse("counselor:termination_record_edit", kwargs={"pk": case.pk})
            ).status_code,
            200,
        )

        done = http.post(
            reverse("counselor:case_records_submit", kwargs={"pk": case.pk}),
            {"confirm": "1"},
            follow=True,
        )
        case.refresh_from_db()
        self.assertEqual(case.status, CaseStatus.CLOSED)
        self.assertEqual(case.termination_type, CaseTerminationType.EARLY)
        self.assertEqual(case.closed_at, closed_at)
        self.assertIsNotNone(case.records_submitted_at)
        self.assertContains(done, "최종 제출 완료")
        self.assertEqual(
            http.get(
                reverse("counselor:journal_edit", kwargs={"pk": case.pk, "session_number": 1})
            ).status_code,
            403,
        )
        self.assertEqual(
            http.get(
                reverse("counselor:termination_record_edit", kwargs={"pk": case.pk})
            ).status_code,
            403,
        )

    def test_existing_closed_blank_and_normal_are_not_relabeled(self):
        counselor = _create_counselor("과거제출상담")
        client = _create_client("과거제출내담")
        blank = _create_case(
            counselor, client, "CASE-OLD-BLANK", status=CaseStatus.CLOSED
        )
        blank.closed_at = timezone.now()
        blank.save(update_fields=["closed_at"])
        _create_termination_record(blank, counselor)
        ok, errors = submit_case_records(blank, counselor)
        self.assertTrue(ok, errors)
        blank.refresh_from_db()
        self.assertEqual(blank.termination_type, "")
        self.assertEqual(blank.status, CaseStatus.CLOSED)

        normal_client = _create_client("정상유지내담")
        normal = _create_case(
            counselor,
            normal_client,
            "CASE-OLD-NORMAL",
            status=CaseStatus.CLOSED,
        )
        normal.termination_type = CaseTerminationType.NORMAL
        normal.closed_at = timezone.now()
        normal.save(update_fields=["termination_type", "closed_at"])
        _create_termination_record(normal, counselor)
        submit_case_records(normal, counselor)
        normal.refresh_from_db()
        first_submitted_at = normal.records_submitted_at
        self.assertEqual(normal.termination_type, CaseTerminationType.NORMAL)

        again, again_errors = submit_case_records(normal, counselor)
        self.assertFalse(again)
        self.assertIn("이미 최종 제출된 사례입니다.", again_errors)
        normal.refresh_from_db()
        self.assertEqual(normal.records_submitted_at, first_submitted_at)
        self.assertEqual(normal.termination_type, CaseTerminationType.NORMAL)
