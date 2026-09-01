"""종결기록지 조기 종결 · 기록 최종 제출 · 제출 후 잠금."""

from datetime import timedelta

from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.counseling.services import finalize_completed_journal
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.sessions_app.models import (
    CounselingJournal,
    InitialCounselingRecord,
    TerminationCounselingRecord,
)


def _create_client(name: str) -> User:
    return User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.CLIENT,
        status=UserStatus.ACTIVE,
    )


def _create_counselor(name: str) -> User:
    user = User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.COUNSELOR,
        status=UserStatus.ACTIVE,
    )
    CounselorProfile.objects.update_or_create(
        user=user,
        defaults={"cohort": 1, "is_approved": True},
    )
    return user


def _create_case(counselor, client, case_number, *, total=3, remaining=3, status=CaseStatus.ACTIVE):
    application = CounselingApplication.objects.create(
        client=client,
        counseling_types=["진로상담"],
        reason="test",
        counseling_method=CounselingMethod.IN_PERSON,
        status=ApplicationStatus.IN_PROGRESS,
    )
    return Case.objects.create(
        application=application,
        client=client,
        counselor=counselor,
        case_number=case_number,
        status=status,
        counseling_method=CounselingMethod.IN_PERSON,
        total_sessions=total,
        remaining_sessions=remaining,
    )


def _create_journal(case, counselor, session_number, *, is_draft=False):
    return CounselingJournal.objects.create(
        case=case,
        counselor=counselor,
        session_number=session_number,
        is_draft=is_draft,
        subjective="S",
        objective="O",
        assessment="A",
        plan="P",
    )


def _create_initial_record(case, counselor):
    return InitialCounselingRecord.objects.create(
        case=case,
        counselor=counselor,
        is_draft=False,
        session_start_datetime=timezone.now() - timedelta(days=30),
        presented_problems_summary="호소 문제",
        clinical_strategy="개입 전략",
    )


def _create_termination_record(case, counselor):
    return TerminationCounselingRecord.objects.create(
        case=case,
        counselor=counselor,
        is_draft=False,
        termination_reason="상담 목표 달성",
    )


class CaseClosureRuleTests(TestCase):
    """종결 조건 — 회기 소진 / 종결기록지 저장."""

    def setUp(self):
        self.counselor = _create_counselor("종결상담사")
        self.client_user = _create_client("종결내담자")
        self.case = _create_case(self.counselor, self.client_user, "CASE-CLOSE-1")
        self.http = HttpClient()
        self.http.force_login(self.counselor)

    def test_sessions_exhausted_closes_case(self):
        for session_number in range(1, 4):
            finalize_completed_journal(
                _create_journal(self.case, self.counselor, session_number)
            )

        self.case.refresh_from_db()
        self.assertEqual(self.case.status, CaseStatus.CLOSED)
        self.assertEqual(self.case.remaining_sessions, 0)
        self.assertIsNotNone(self.case.closed_at)
        self.assertEqual(self.case.application.status, ApplicationStatus.CLOSED)

    def test_termination_record_closes_case_and_preserves_remaining_sessions(self):
        response = self.http.post(
            reverse("counselor:termination_record_create", kwargs={"pk": self.case.pk}),
            {
                "counseling_period": "2026-01-01",
                "main_topics": "주요 주제",
                "termination_reason": "목표 달성",
                "counselor_opinion": "소견",
                "post_termination_plan": "계획",
                "other_notes": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, CaseStatus.CLOSED)
        self.assertIsNotNone(self.case.closed_at)
        self.assertEqual(self.case.remaining_sessions, 3)
        self.assertEqual(self.case.application.status, ApplicationStatus.CLOSED)

    def test_termination_record_edit_keeps_original_closed_at(self):
        record = _create_termination_record(self.case, self.counselor)
        original_closed_at = timezone.now() - timedelta(days=3)
        self.case.status = CaseStatus.CLOSED
        self.case.closed_at = original_closed_at
        self.case.save(update_fields=["status", "closed_at"])

        response = self.http.post(
            reverse("counselor:termination_record_edit", kwargs={"pk": self.case.pk}),
            {
                "counseling_period": "2026-01-02",
                "main_topics": "수정된 주제",
                "termination_reason": "수정된 사유",
                "counselor_opinion": "",
                "post_termination_plan": "",
                "other_notes": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.case.refresh_from_db()
        record.refresh_from_db()
        self.assertEqual(self.case.closed_at, original_closed_at)
        self.assertEqual(record.main_topics, "수정된 주제")


class ClosedCaseBeforeSubmitTests(TestCase):
    """종결 후 · 최종 제출 전 — 기존 기능 유지. 잠금 기준은 최종 제출."""

    def setUp(self):
        self.counselor = _create_counselor("잠금전상담사")
        self.client_user = _create_client("잠금전내담자")
        self.case = _create_case(
            self.counselor, self.client_user, "CASE-OPEN-1", status=CaseStatus.CLOSED
        )
        self.case.closed_at = timezone.now()
        self.case.save(update_fields=["closed_at"])
        self.journal = _create_journal(self.case, self.counselor, 1)
        _create_termination_record(self.case, self.counselor)
        self.http = HttpClient()
        self.http.force_login(self.counselor)

    def test_existing_journal_is_editable(self):
        response = self.http.get(
            reverse(
                "counselor:journal_edit",
                kwargs={"pk": self.case.pk, "session_number": 1},
            )
        )
        self.assertEqual(response.status_code, 200)

    def test_termination_record_is_editable(self):
        response = self.http.get(
            reverse("counselor:termination_record_edit", kwargs={"pk": self.case.pk})
        )
        self.assertEqual(response.status_code, 200)

    def test_new_journal_is_allowed_when_closed_but_not_submitted(self):
        response = self.http.get(
            reverse("counselor:journal_create", kwargs={"pk": self.case.pk})
        )
        self.assertEqual(response.status_code, 200)

    def test_new_booking_is_allowed_when_closed_but_not_submitted(self):
        response = self.http.get(
            reverse(
                "counselor:session_appointment_book",
                kwargs={"case_pk": self.case.pk, "session_number": 2},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Appointment.objects.filter(case=self.case).count(), 0)

    def test_client_booking_calendar_is_allowed_when_closed_but_not_submitted(self):
        client_http = HttpClient()
        client_http.force_login(self.client_user)

        response = client_http.get(
            reverse(
                "client:session_booking_calendar",
                kwargs={"case_pk": self.case.pk, "session_number": 2},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Appointment.objects.filter(case=self.case).count(), 0)


class RecordsSubmitButtonTests(TestCase):
    """최종 제출 버튼은 조건과 무관하게 항상 노출."""

    def setUp(self):
        self.counselor = _create_counselor("버튼상담사")
        self.client_user = _create_client("버튼내담자")
        self.case = _create_case(self.counselor, self.client_user, "CASE-BTN-1")
        self.http = HttpClient()
        self.http.force_login(self.counselor)
        self.url = reverse("counselor:case_detail", kwargs={"pk": self.case.pk})

    def test_button_visible_on_active_case(self):
        response = self.http.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_submit_records"])
        self.assertFalse(response.context["records_submitted"])
        self.assertContains(response, "최종 제출")

    def test_button_visible_without_termination_record(self):
        self.case.status = CaseStatus.CLOSED
        self.case.save(update_fields=["status"])

        response = self.http.get(self.url)

        self.assertFalse(TerminationCounselingRecord.objects.filter(case=self.case).exists())
        self.assertContains(response, "최종 제출")

    def test_button_visible_with_draft_journal(self):
        _create_journal(self.case, self.counselor, 1, is_draft=True)

        response = self.http.get(self.url)

        self.assertContains(response, "최종 제출")

    def test_submitted_case_shows_completed_state_instead_of_button(self):
        self.case.records_submitted_at = timezone.now()
        self.case.records_submitted_by = self.counselor
        self.case.save(update_fields=["records_submitted_at", "records_submitted_by"])

        response = self.http.get(self.url)

        self.assertTrue(response.context["records_submitted"])
        self.assertContains(response, "최종 제출 완료")
        self.assertNotContains(
            response,
            reverse("counselor:case_records_submit", kwargs={"pk": self.case.pk}),
        )


class RecordsSubmitValidationTests(TestCase):
    """제출 조건 검사 — 미충족 사유를 모두 안내하고 DB는 변경하지 않음."""

    def setUp(self):
        self.counselor = _create_counselor("검증상담사")
        self.client_user = _create_client("검증내담자")
        self.case = _create_case(self.counselor, self.client_user, "CASE-VAL-1")
        self.http = HttpClient()
        self.http.force_login(self.counselor)
        self.submit_url = reverse(
            "counselor:case_records_submit", kwargs={"pk": self.case.pk}
        )

    def _post(self, **data):
        return self.http.post(self.submit_url, data, follow=True)

    def test_active_case_reports_not_closed(self):
        response = self._post()

        errors = response.context["records_submit_errors"]
        self.assertTrue(any("아직 종결되지 않았습니다" in e for e in errors))
        self.assertContains(response, "최종 제출할 수 없습니다.")

    def test_missing_termination_record_is_reported(self):
        response = self._post()

        errors = response.context["records_submit_errors"]
        self.assertIn("종결기록지가 작성되지 않았습니다.", errors)

    def test_draft_journal_count_is_reported(self):
        _create_journal(self.case, self.counselor, 1, is_draft=True)
        _create_journal(self.case, self.counselor, 2, is_draft=True)

        response = self._post()

        errors = response.context["records_submit_errors"]
        self.assertTrue(
            any("임시저장 상태의 상담일지가 2건 있습니다." in e for e in errors)
        )

    def test_all_unmet_conditions_are_reported_together(self):
        _create_journal(self.case, self.counselor, 1, is_draft=True)

        response = self._post()

        errors = response.context["records_submit_errors"]
        self.assertEqual(len(errors), 3)

    def test_failed_submit_does_not_change_db(self):
        self._post()

        self.case.refresh_from_db()
        self.assertIsNone(self.case.records_submitted_at)
        self.assertIsNone(self.case.records_submitted_by)
        self.assertEqual(self.case.status, CaseStatus.ACTIVE)

    def test_valid_conditions_show_confirm_dialog_without_saving(self):
        self.case.status = CaseStatus.CLOSED
        self.case.save(update_fields=["status"])
        _create_termination_record(self.case, self.counselor)

        response = self._post()

        self.assertTrue(response.context["records_submit_confirm"])
        self.assertEqual(response.context["records_submit_errors"], [])
        self.assertContains(response, "최종 제출하시겠습니까?")
        self.case.refresh_from_db()
        self.assertIsNone(self.case.records_submitted_at)

    def test_confirmed_submit_saves_submitter_and_timestamp(self):
        self.case.status = CaseStatus.CLOSED
        self.case.save(update_fields=["status"])
        _create_termination_record(self.case, self.counselor)

        response = self._post(confirm="1")

        self.case.refresh_from_db()
        self.assertIsNotNone(self.case.records_submitted_at)
        self.assertEqual(self.case.records_submitted_by, self.counselor)
        self.assertContains(response, "최종 제출되었습니다.")

    def test_duplicate_submit_is_idempotent(self):
        self.case.status = CaseStatus.CLOSED
        self.case.save(update_fields=["status"])
        _create_termination_record(self.case, self.counselor)

        self._post(confirm="1")
        self.case.refresh_from_db()
        first_submitted_at = self.case.records_submitted_at

        response = self._post(confirm="1")

        self.case.refresh_from_db()
        self.assertEqual(self.case.records_submitted_at, first_submitted_at)
        self.assertIn("이미 최종 제출된 사례입니다.", response.context["records_submit_errors"])

    def test_server_revalidates_before_saving(self):
        self.case.status = CaseStatus.CLOSED
        self.case.save(update_fields=["status"])
        record = _create_termination_record(self.case, self.counselor)

        confirm_response = self._post()
        self.assertTrue(confirm_response.context["records_submit_confirm"])

        record.delete()
        response = self._post(confirm="1")

        self.case.refresh_from_db()
        self.assertIsNone(self.case.records_submitted_at)
        self.assertIn(
            "종결기록지가 작성되지 않았습니다.",
            response.context["records_submit_errors"],
        )

    def test_other_counselor_cannot_submit(self):
        other = _create_counselor("타상담사")
        other_http = HttpClient()
        other_http.force_login(other)

        response = other_http.post(self.submit_url, {"confirm": "1"})

        self.assertEqual(response.status_code, 404)
        self.case.refresh_from_db()
        self.assertIsNone(self.case.records_submitted_at)


class SubmittedCaseLockTests(TestCase):
    """최종 제출 후 잠금 — 조회는 가능, 생성·수정은 서버에서 차단."""

    def setUp(self):
        self.counselor = _create_counselor("잠금상담사")
        self.client_user = _create_client("잠금내담자")
        self.case = _create_case(
            self.counselor, self.client_user, "CASE-LOCK-1", status=CaseStatus.CLOSED
        )
        self.case.closed_at = timezone.now()
        self.case.records_submitted_at = timezone.now()
        self.case.records_submitted_by = self.counselor
        self.case.save(
            update_fields=["closed_at", "records_submitted_at", "records_submitted_by"]
        )
        self.journal = _create_journal(self.case, self.counselor, 1)
        _create_termination_record(self.case, self.counselor)
        self.http = HttpClient()
        self.http.force_login(self.counselor)

    def test_records_remain_viewable(self):
        journal_detail = self.http.get(
            reverse(
                "counselor:journal_detail",
                kwargs={"pk": self.case.pk, "session_number": 1},
            )
        )
        termination_detail = self.http.get(
            reverse("counselor:termination_record_detail", kwargs={"pk": self.case.pk})
        )
        case_detail = self.http.get(
            reverse("counselor:case_detail", kwargs={"pk": self.case.pk})
        )

        self.assertEqual(journal_detail.status_code, 200)
        self.assertEqual(termination_detail.status_code, 200)
        self.assertEqual(case_detail.status_code, 200)

    def test_edit_buttons_are_hidden(self):
        journal_detail = self.http.get(
            reverse(
                "counselor:journal_detail",
                kwargs={"pk": self.case.pk, "session_number": 1},
            )
        )
        termination_detail = self.http.get(
            reverse("counselor:termination_record_detail", kwargs={"pk": self.case.pk})
        )

        self.assertFalse(journal_detail.context["can_edit"])
        self.assertFalse(termination_detail.context["can_edit"])

    def test_journal_edit_blocked_by_url_and_post(self):
        url = reverse(
            "counselor:journal_edit",
            kwargs={"pk": self.case.pk, "session_number": 1},
        )

        self.assertEqual(self.http.get(url).status_code, 403)
        self.assertEqual(self.http.post(url, {}).status_code, 403)
        self.journal.refresh_from_db()
        self.assertEqual(self.journal.subjective, "S")

    def test_journal_create_blocked(self):
        url = reverse("counselor:journal_create", kwargs={"pk": self.case.pk})

        self.assertEqual(self.http.get(url).status_code, 403)
        self.assertEqual(self.http.post(url, {}).status_code, 403)
        self.assertEqual(CounselingJournal.objects.filter(case=self.case).count(), 1)

    def test_termination_record_edit_blocked(self):
        url = reverse("counselor:termination_record_edit", kwargs={"pk": self.case.pk})

        self.assertEqual(self.http.get(url).status_code, 403)
        self.assertEqual(
            self.http.post(url, {"termination_reason": "변경 시도"}).status_code, 403
        )
        record = TerminationCounselingRecord.objects.get(case=self.case)
        self.assertEqual(record.termination_reason, "상담 목표 달성")

    def test_new_booking_blocked(self):
        response = self.http.get(
            reverse(
                "counselor:session_appointment_book",
                kwargs={"case_pk": self.case.pk, "session_number": 2},
            )
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Appointment.objects.filter(case=self.case).count(), 0)

    def test_session_card_hides_write_actions(self):
        response = self.http.get(
            reverse("counselor:case_detail", kwargs={"pk": self.case.pk})
        )

        sessions = response.context["sessions"]
        self.assertTrue(sessions[0].show_counselor_journal)
        self.assertFalse(sessions[1].show_counselor_journal)
        self.assertFalse(any(s.show_counselor_direct_booking for s in sessions))
        self.assertNotContains(
            response,
            reverse("counselor:journal_create", kwargs={"pk": self.case.pk}),
        )

    def test_other_counselor_cannot_access_submitted_case(self):
        other = _create_counselor("잠금타상담사")
        other_http = HttpClient()
        other_http.force_login(other)

        detail = other_http.get(
            reverse("counselor:case_detail", kwargs={"pk": self.case.pk})
        )
        journal = other_http.get(
            reverse(
                "counselor:journal_detail",
                kwargs={"pk": self.case.pk, "session_number": 1},
            )
        )

        self.assertEqual(detail.status_code, 404)
        self.assertEqual(journal.status_code, 404)


class InitialRecordLockTests(TestCase):
    """초기상담 기록지 — 최종 제출 전에는 작성·수정 가능, 제출 후에는 열람만."""

    def setUp(self):
        self.counselor = _create_counselor("초기기록상담사")
        self.client_user = _create_client("초기기록내담자")
        self.case = _create_case(
            self.counselor, self.client_user, "CASE-INIT-1", status=CaseStatus.CLOSED
        )
        self.case.closed_at = timezone.now()
        self.case.save(update_fields=["closed_at"])
        _create_journal(self.case, self.counselor, 1)
        _create_termination_record(self.case, self.counselor)
        self.http = HttpClient()
        self.http.force_login(self.counselor)
        self.detail_url = reverse("counselor:case_detail", kwargs={"pk": self.case.pk})
        self.create_url = reverse(
            "counselor:initial_record_create", kwargs={"pk": self.case.pk}
        )
        self.edit_url = reverse(
            "counselor:initial_record_edit", kwargs={"pk": self.case.pk}
        )

    def _submit(self):
        self.case.records_submitted_at = timezone.now()
        self.case.records_submitted_by = self.counselor
        self.case.save(update_fields=["records_submitted_at", "records_submitted_by"])

    def test_create_and_edit_work_before_submit(self):
        self.assertEqual(self.http.get(self.create_url).status_code, 200)

        record = _create_initial_record(self.case, self.counselor)
        self.assertEqual(self.http.get(self.edit_url).status_code, 200)

        response = self.http.post(
            self.edit_url,
            {
                "session_start_datetime": "2026-01-05 10:00",
                "presented_problems_summary": "수정된 호소 문제",
                "functioning_impact": "",
                "relational_history": "",
                "clinical_history": "",
                "theological_evaluation": "",
                "clinical_strategy": "전략",
                "other_notes": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.presented_problems_summary, "수정된 호소 문제")

    def test_create_button_hidden_after_submit(self):
        self._submit()

        response = self.http.get(self.detail_url)

        sessions = response.context["sessions"]
        self.assertFalse(sessions[0].show_initial_record)
        self.assertNotContains(response, self.create_url)

    def test_edit_button_hidden_after_submit(self):
        _create_initial_record(self.case, self.counselor)
        self._submit()

        detail = self.http.get(
            reverse("counselor:initial_record_detail", kwargs={"pk": self.case.pk})
        )
        case_detail = self.http.get(self.detail_url)

        self.assertFalse(detail.context["can_edit"])
        self.assertNotContains(detail, self.edit_url)
        self.assertNotContains(case_detail, self.edit_url)

    def test_create_blocked_by_direct_url_after_submit(self):
        self._submit()

        self.assertEqual(self.http.get(self.create_url).status_code, 403)
        self.assertEqual(self.http.post(self.create_url, {}).status_code, 403)
        self.assertFalse(InitialCounselingRecord.objects.filter(case=self.case).exists())

    def test_edit_blocked_by_direct_url_after_submit(self):
        _create_initial_record(self.case, self.counselor)
        self._submit()

        self.assertEqual(self.http.get(self.edit_url).status_code, 403)
        self.assertEqual(
            self.http.post(
                self.edit_url,
                {
                    "session_start_datetime": "2026-01-05 10:00",
                    "presented_problems_summary": "변경 시도",
                    "clinical_strategy": "변경 시도",
                },
            ).status_code,
            403,
        )
        record = InitialCounselingRecord.objects.get(case=self.case)
        self.assertEqual(record.presented_problems_summary, "호소 문제")

    def test_existing_record_remains_viewable_after_submit(self):
        _create_initial_record(self.case, self.counselor)
        self._submit()

        response = self.http.get(
            reverse("counselor:initial_record_detail", kwargs={"pk": self.case.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "호소 문제")
        self.assertContains(
            response,
            reverse("counselor:initial_record_detail", kwargs={"pk": self.case.pk}),
        )


class RecordsTerminologyTests(TestCase):
    """사용자 표시 문구는 '종결기록지'로 통일."""

    def setUp(self):
        self.counselor = _create_counselor("용어상담사")
        self.client_user = _create_client("용어내담자")
        self.case = _create_case(self.counselor, self.client_user, "CASE-TERM-1")
        self.http = HttpClient()
        self.http.force_login(self.counselor)
        self.submit_url = reverse(
            "counselor:case_records_submit", kwargs={"pk": self.case.pk}
        )

    def _close_with_records(self):
        self.case.status = CaseStatus.CLOSED
        self.case.save(update_fields=["status"])
        _create_termination_record(self.case, self.counselor)

    def test_blocked_dialog_uses_termination_record_term(self):
        response = self.http.post(self.submit_url, {}, follow=True)

        errors = response.context["records_submit_errors"]
        self.assertIn("종결기록지가 작성되지 않았습니다.", errors)
        self.assertIn(
            "상담이 아직 종결되지 않았습니다. 회기 차감 완료 또는 종결기록지 작성 후 제출해 주세요.",
            errors,
        )
        self.assertNotContains(response, "종결일지")

    def test_confirm_dialog_uses_termination_record_term(self):
        self._close_with_records()

        response = self.http.post(self.submit_url, {}, follow=True)

        self.assertContains(
            response,
            "최종 제출 후에는 초기상담 기록지, 상담일지, 종결기록지 및 해당 사례의 모든 정보를 수정할 수 없습니다. 최종 제출하시겠습니까?",
        )
        self.assertNotContains(response, "종결일지")

    def test_success_message_uses_termination_record_term(self):
        self._close_with_records()

        response = self.http.post(self.submit_url, {"confirm": "1"}, follow=True)

        self.assertContains(
            response,
            "최종 제출되었습니다. 제출된 사례의 모든 정보는 더 이상 수정할 수 없습니다.",
        )
        self.assertNotContains(response, "종결일지")

    def test_case_detail_never_uses_journal_term_for_termination_record(self):
        self._close_with_records()

        response = self.http.get(
            reverse("counselor:case_detail", kwargs={"pk": self.case.pk})
        )

        self.assertContains(response, "종결기록지")
        self.assertNotContains(response, "종결일지")


class AppointmentServiceGuardTests(TestCase):
    """예약 생성 서비스는 최종 제출된 사례만 차단합니다."""

    def setUp(self):
        self.counselor = _create_counselor("예약상담사")
        self.client_user = _create_client("예약내담자")
        self.case = _create_case(self.counselor, self.client_user, "CASE-APT-1")

    def test_create_appointment_request_allowed_for_closed_unsubmitted_case(self):
        from apps.scheduling.services import _assert_case_accepts_new_appointment

        self.case.status = CaseStatus.CLOSED
        self.case.save(update_fields=["status"])

        _assert_case_accepts_new_appointment(self.case)

    def test_create_appointment_request_blocked_for_submitted_case(self):
        from apps.scheduling.services import (
            AppointmentServiceError,
            _assert_case_accepts_new_appointment,
        )

        self.case.status = CaseStatus.CLOSED
        self.case.records_submitted_at = timezone.now()
        self.case.save(update_fields=["status", "records_submitted_at"])

        with self.assertRaises(AppointmentServiceError):
            _assert_case_accepts_new_appointment(self.case)


def _create_admin(name: str) -> User:
    return User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )


def _create_confirmed_appointment(case, *, session_number=2):
    return Appointment.objects.create(
        case=case,
        counselor=case.counselor,
        client=case.client,
        scheduled_at=timezone.now() + timedelta(days=7),
        duration_minutes=50,
        status=AppointmentStatus.CONFIRMED,
        session_number=session_number,
    )


class SubmittedCaseMutationLockTests(TestCase):
    """최종 제출 후 — 조회는 가능, 모든 생성·수정·삭제는 서버에서 차단."""

    def setUp(self):
        self.counselor = _create_counselor("제출후상담사")
        self.client_user = _create_client("제출후내담자")
        self.admin = _create_admin("제출후관리자")
        self.case = _create_case(
            self.counselor, self.client_user, "CASE-MUT-1", status=CaseStatus.CLOSED
        )
        self.case.closed_at = timezone.now()
        self.case.records_submitted_at = timezone.now()
        self.case.records_submitted_by = self.counselor
        self.case.save(
            update_fields=["closed_at", "records_submitted_at", "records_submitted_by"]
        )
        self.journal = _create_journal(self.case, self.counselor, 1)
        _create_termination_record(self.case, self.counselor)
        _create_initial_record(self.case, self.counselor)
        self.appointment = _create_confirmed_appointment(self.case)
        self.http = HttpClient()
        self.http.force_login(self.counselor)
        self.client_http = HttpClient()
        self.client_http.force_login(self.client_user)
        self.admin_http = HttpClient()
        self.admin_http.force_login(self.admin)

    def test_pdf_and_records_remain_viewable(self):
        journal_detail = self.http.get(
            reverse(
                "counselor:journal_detail",
                kwargs={"pk": self.case.pk, "session_number": 1},
            )
        )
        initial_detail = self.http.get(
            reverse("counselor:initial_record_detail", kwargs={"pk": self.case.pk})
        )
        termination_detail = self.http.get(
            reverse("counselor:termination_record_detail", kwargs={"pk": self.case.pk})
        )
        client_detail = self.client_http.get(
            reverse("client:case_detail", kwargs={"pk": self.case.pk})
        )

        self.assertEqual(journal_detail.status_code, 200)
        self.assertEqual(initial_detail.status_code, 200)
        self.assertEqual(termination_detail.status_code, 200)
        self.assertEqual(client_detail.status_code, 200)
        self.assertContains(client_detail, "종결")

    def test_appointment_cancel_blocked(self):
        url = reverse(
            "counselor:session_appointment_cancel",
            kwargs={"case_pk": self.case.pk, "appointment_pk": self.appointment.pk},
        )
        response = self.http.post(url, {"cancel_reason": "최종 제출 후 취소 시도입니다."})
        self.assertEqual(response.status_code, 403)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, AppointmentStatus.CONFIRMED)

    def test_appointment_confirm_blocked(self):
        pending = Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=timezone.now() + timedelta(days=10),
            duration_minutes=50,
            status=AppointmentStatus.PENDING,
            session_number=3,
        )
        url = reverse(
            "counselor:session_appointment_confirm",
            kwargs={"case_pk": self.case.pk, "appointment_pk": pending.pk},
        )
        self.assertEqual(self.http.post(url, {}).status_code, 403)
        pending.refresh_from_db()
        self.assertEqual(pending.status, AppointmentStatus.PENDING)

    def test_appointment_reject_blocked(self):
        pending = Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=timezone.now() + timedelta(days=11),
            duration_minutes=50,
            status=AppointmentStatus.PENDING,
            session_number=3,
        )
        url = reverse(
            "counselor:session_appointment_reject",
            kwargs={"case_pk": self.case.pk, "appointment_pk": pending.pk},
        )
        self.assertEqual(
            self.http.post(url, {"reject_reason": "제출 후 반려 시도"}).status_code,
            403,
        )
        pending.refresh_from_db()
        self.assertEqual(pending.status, AppointmentStatus.PENDING)

    def test_material_upload_and_delete_blocked(self):
        upload = reverse(
            "counselor:session_material_upload",
            kwargs={"case_pk": self.case.pk, "session_number": 2},
        )
        self.assertEqual(self.http.post(upload, {}).status_code, 403)

        from apps.documents.models import SessionMaterial

        material = SessionMaterial.objects.create(
            case=self.case,
            session_number=2,
            appointment=self.appointment,
            title="기존 자료",
            uploaded_by=self.counselor,
        )
        delete_url = reverse(
            "counselor:session_material_delete",
            kwargs={
                "case_pk": self.case.pk,
                "session_number": 2,
                "material_pk": material.pk,
            },
        )
        self.assertEqual(self.http.post(delete_url, {}).status_code, 403)
        self.assertTrue(SessionMaterial.objects.filter(pk=material.pk).exists())

    def test_application_edit_blocked(self):
        url = reverse(
            "client:edit_application", kwargs={"pk": self.case.application_id}
        )
        self.assertEqual(self.client_http.post(url, {}).status_code, 403)

    def test_reassign_counselor_blocked(self):
        other = _create_counselor("재배정대상")
        url = reverse(
            "counseling:application_detail", kwargs={"pk": self.case.application_id}
        )
        response = self.admin_http.post(url, {"counselor": str(other.pk)})
        self.assertEqual(response.status_code, 403)
        self.case.refresh_from_db()
        self.assertEqual(self.case.counselor_id, self.counselor.pk)

    def test_chat_send_blocked_but_messages_viewable(self):
        send_url = reverse("counselor:case_chat_send", kwargs={"pk": self.case.pk})
        messages_url = reverse(
            "counselor:case_chat_messages", kwargs={"pk": self.case.pk}
        )
        response = self.http.post(
            send_url, data='{"body": "제출 후 채팅"}', content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.http.get(messages_url).status_code, 200)

    def test_admin_direct_case_save_blocked(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory

        from apps.counseling.admin import CaseAdmin

        factory = RequestFactory()
        request = factory.post("/admin/")
        request.user = self.admin
        admin_model = CaseAdmin(Case, AdminSite())
        self.assertFalse(admin_model.has_change_permission(request, self.case))
        self.assertFalse(admin_model.has_delete_permission(request, self.case))

    def test_dashboards_keep_submitted_case(self):
        counselor_dash = self.http.get(reverse("counselor:dashboard"))
        client_dash = self.client_http.get(reverse("client:dashboard"))

        self.assertContains(counselor_dash, "CASE-MUT-1")
        self.assertContains(client_dash, "CASE-MUT-1")
        self.assertContains(client_dash, "종결")


class AdminRecordsUnsubmitTests(TestCase):
    """관리자만 최종 제출을 취소할 수 있고, 기존 데이터는 유지됩니다."""

    def setUp(self):
        self.counselor = _create_counselor("취소상담사")
        self.client_user = _create_client("취소내담자")
        self.admin = _create_admin("취소관리자")
        self.case = _create_case(
            self.counselor, self.client_user, "CASE-UNSUB-1", status=CaseStatus.CLOSED
        )
        self.case.closed_at = timezone.now()
        self.case.records_submitted_at = timezone.now()
        self.case.records_submitted_by = self.counselor
        self.case.save(
            update_fields=["closed_at", "records_submitted_at", "records_submitted_by"]
        )
        self.journal = _create_journal(self.case, self.counselor, 1)
        self.termination = _create_termination_record(self.case, self.counselor)
        self.initial = _create_initial_record(self.case, self.counselor)
        self.appointment = _create_confirmed_appointment(self.case)
        self.url = reverse(
            "counseling:case_records_unsubmit", kwargs={"pk": self.case.pk}
        )
        self.admin_http = HttpClient()
        self.admin_http.force_login(self.admin)

    def test_button_visible_only_to_admin_on_submitted_case(self):
        counselor_http = HttpClient()
        counselor_http.force_login(self.counselor)
        client_http = HttpClient()
        client_http.force_login(self.client_user)

        admin_detail = self.admin_http.get(
            reverse("counselor:case_detail", kwargs={"pk": self.case.pk})
        )
        counselor_detail = counselor_http.get(
            reverse("counselor:case_detail", kwargs={"pk": self.case.pk})
        )
        client_detail = client_http.get(
            reverse("client:case_detail", kwargs={"pk": self.case.pk})
        )
        management = self.admin_http.get(
            reverse("admin_panel:counseling_management") + "?tab=closed"
        )

        self.assertContains(admin_detail, "최종 제출 취소")
        self.assertNotContains(counselor_detail, "최종 제출 취소")
        self.assertNotContains(client_detail, "최종 제출 취소")
        self.assertContains(management, "최종 제출 취소")

    def test_counselor_and_client_post_blocked(self):
        counselor_http = HttpClient()
        counselor_http.force_login(self.counselor)
        client_http = HttpClient()
        client_http.force_login(self.client_user)

        self.assertEqual(
            counselor_http.post(self.url, {"confirm": "1"}).status_code, 403
        )
        self.assertEqual(client_http.post(self.url, {"confirm": "1"}).status_code, 403)
        self.case.refresh_from_db()
        self.assertIsNotNone(self.case.records_submitted_at)

    def test_admin_unsubmit_clears_only_submission_fields(self):
        journal_id = self.journal.pk
        termination_id = self.termination.pk
        initial_id = self.initial.pk
        appointment_id = self.appointment.pk
        journal_text = self.journal.subjective
        appointment_status = self.appointment.status

        response = self.admin_http.post(
            self.url, {"confirm": "1"}, follow=True
        )

        self.case.refresh_from_db()
        self.journal.refresh_from_db()
        self.termination.refresh_from_db()
        self.initial.refresh_from_db()
        self.appointment.refresh_from_db()

        self.assertEqual(self.case.status, CaseStatus.CLOSED)
        self.assertIsNone(self.case.records_submitted_at)
        self.assertIsNone(self.case.records_submitted_by)
        self.assertEqual(self.journal.pk, journal_id)
        self.assertEqual(self.termination.pk, termination_id)
        self.assertEqual(self.initial.pk, initial_id)
        self.assertEqual(self.appointment.pk, appointment_id)
        self.assertEqual(self.journal.subjective, journal_text)
        self.assertEqual(self.appointment.status, appointment_status)
        self.assertEqual(CounselingJournal.objects.filter(case=self.case).count(), 1)
        self.assertContains(
            response,
            "최종 제출이 취소되었습니다. 상담사가 사례 정보를 다시 수정할 수 있습니다.",
        )

    def test_counselor_can_edit_and_resubmit_after_unsubmit(self):
        self.admin_http.post(self.url, {"confirm": "1"})
        self.case.refresh_from_db()

        counselor_http = HttpClient()
        counselor_http.force_login(self.counselor)
        edit = counselor_http.get(
            reverse(
                "counselor:journal_edit",
                kwargs={"pk": self.case.pk, "session_number": 1},
            )
        )
        self.assertEqual(edit.status_code, 200)

        submit_url = reverse(
            "counselor:case_records_submit", kwargs={"pk": self.case.pk}
        )
        confirm = counselor_http.post(submit_url, {}, follow=True)
        self.assertTrue(confirm.context["records_submit_confirm"])
        done = counselor_http.post(submit_url, {"confirm": "1"}, follow=True)
        self.case.refresh_from_db()
        self.assertIsNotNone(self.case.records_submitted_at)
        self.assertEqual(self.case.records_submitted_by, self.counselor)
        self.assertContains(done, "최종 제출되었습니다.")


class ClientDashboardClosedCaseTests(TestCase):
    """내담자 대시보드·사례 상세에 본인 종결 사례가 유지됩니다."""

    def setUp(self):
        self.counselor = _create_counselor("내담자대시상담사")
        self.client_user = _create_client("내담자대시내담자")
        self.other_client = _create_client("다른내담자")
        self.other_counselor = _create_counselor("다른상담사")
        self.closed_case = _create_case(
            self.counselor,
            self.client_user,
            "CASE-CLIENT-CLOSED",
            status=CaseStatus.CLOSED,
        )
        self.closed_case.closed_at = timezone.now()
        self.closed_case.save(update_fields=["closed_at"])
        self.other_closed = _create_case(
            self.other_counselor,
            self.other_client,
            "CASE-OTHER-CLOSED",
            status=CaseStatus.CLOSED,
        )
        self.http = HttpClient()
        self.http.force_login(self.client_user)

    def test_client_dashboard_lists_own_closed_case_as_closed(self):
        response = self.http.get(reverse("client:dashboard"))

        self.assertContains(response, "CASE-CLIENT-CLOSED")
        self.assertContains(response, "종결")
        self.assertNotContains(response, "CASE-OTHER-CLOSED")
        closed_pks = [case.pk for case in response.context["closed_cases"]]
        self.assertEqual(closed_pks, [self.closed_case.pk])

    def test_client_case_detail_shows_closed_status(self):
        response = self.http.get(
            reverse("client:case_detail", kwargs={"pk": self.closed_case.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "종결")
        self.assertEqual(response.context["case"].get_status_display(), "종결")

    def test_submitted_closed_case_remains_on_client_dashboard(self):
        self.closed_case.records_submitted_at = timezone.now()
        self.closed_case.records_submitted_by = self.counselor
        self.closed_case.save(
            update_fields=["records_submitted_at", "records_submitted_by"]
        )

        response = self.http.get(reverse("client:dashboard"))
        self.assertContains(response, "CASE-CLIENT-CLOSED")

