"""수련확인증은 최종 제출 Case와 승인 hash를 기준으로 발급한다."""

from datetime import datetime
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CasePresentationPost,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
    SupervisionAttendance,
    SupervisionAttendanceStatus,
    SupervisionSession,
    SupervisionSessionStatus,
    TrainingCertificateApproval,
)
from apps.counseling.services import submit_case_records
from apps.counseling.training_certificate import (
    MESSAGE_APPROVAL_REQUIRES_SUBMITTED_CASE,
    MESSAGE_NO_SUBMITTED_CASE,
    MESSAGE_REAPPROVAL,
    MESSAGE_UNAPPROVED,
    STATUS_APPROVED,
    STATUS_REAPPROVAL_REQUIRED,
    STATUS_UNAPPROVED,
    approve_training_certificate,
    build_counselor_training_certificate_context,
    calculate_training_certificate_hash,
    certificate_date_for_counselor,
    get_training_certificate_approval_status,
)
from apps.counseling.training_certificate_pdf import format_certificate_date
from apps.counseling.training_progress import (
    TRAINING_CREDIT_MINUTES_PER_SESSION,
    get_counselor_training_progress,
)
from apps.documents.models import ConsentDocType, ConsentDocument
from apps.sessions_app.models import CounselingJournal, TerminationCounselingRecord


def _user(email, name, role):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


def _counselor(name, cohort=4):
    user = _user(f"{name}@cert.example.com", name, UserRole.COUNSELOR)
    profile = user.counselor_profile
    profile.cohort = cohort
    profile.is_approved = True
    profile.save(update_fields=["cohort", "is_approved", "updated_at"])
    return user


def _supervisor(email, name, cohorts):
    user = _user(email, name, UserRole.SUPERVISOR)
    profile = user.supervisor_profile
    profile.assigned_cohorts = cohorts
    profile.save(update_fields=["assigned_cohorts", "updated_at"])
    return user


def _client(name):
    return _user(f"{name}@client.example.com", name, UserRole.CLIENT)


def _case(counselor, client, number, method=CounselingMethod.IN_PERSON, sessions=10):
    application = CounselingApplication.objects.create(
        client=client,
        counseling_types=["진로상담"],
        reason="test",
        counseling_method=method,
        status=ApplicationStatus.IN_PROGRESS,
    )
    return Case.objects.create(
        application=application,
        client=client,
        counselor=counselor,
        case_number=number,
        counseling_method=method,
        total_sessions=sessions,
        remaining_sessions=sessions,
        status=CaseStatus.ACTIVE,
    )


def _submit(case):
    case.records_submitted_at = timezone.now()
    case.records_submitted_by = case.counselor
    case.save(update_fields=["records_submitted_at", "records_submitted_by"])
    return case


def _journals(case, count, *, method=CounselingMethod.IN_PERSON, draft=False):
    start = case.journals.count() + 1
    for offset in range(count):
        CounselingJournal.objects.create(
            case=case,
            counselor=case.counselor,
            session_number=start + offset,
            is_draft=draft,
            counseling_method=method,
        )


def _intake(case, *, method=None):
    ConsentDocument.objects.create(
        client=case.client,
        application=case.application,
        doc_type=ConsentDocType.INTAKE,
        file="consents/intake.pdf",
        uploaded_by=case.counselor,
    )
    if method is not None:
        Case.objects.filter(pk=case.pk).update(intake_counseling_method=method)


def _session(cohort, number, status, minutes, *, method=None, when=None):
    return SupervisionSession.objects.create(
        cohort=cohort,
        session_number=number,
        scheduled_at=when or timezone.now(),
        status=status,
        duration_minutes=minutes,
        counseling_method=method,
    )


def _present(session, counselor):
    return SupervisionAttendance.objects.create(
        session=session,
        counselor=counselor.counselor_profile,
        status=SupervisionAttendanceStatus.PRESENT,
    )


def _report(author, title, *, case, cohort):
    return CasePresentationPost.objects.create(
        cohort=cohort,
        author=author,
        case=case,
        title=title,
        file=SimpleUploadedFile(f"{title}.pdf", b"%PDF-1.4"),
    )


def _bucket_minutes(context, prefix):
    progress = context["progress"]
    return (
        progress[f"{prefix}_in_person_minutes"]
        + progress[f"{prefix}_remote_minutes"]
        + progress[f"{prefix}_unknown_minutes"]
    )


def _http(user):
    client = HttpClient()
    client.force_login(user)
    return client


def _assert_blocked(test, response):
    test.assertEqual(response.status_code, 403)
    test.assertNotEqual(response.get("Content-Type"), "application/pdf")
    test.assertFalse(response.content.startswith(b"%PDF"))


class CertificateSubmittedCaseFilterTests(TestCase):
    def test_counseling_hours_include_only_submitted_cases(self):
        counselor = _counselor("시간필터")
        submitted = _case(counselor, _client("시간A"), "CF-A")
        pending = _case(counselor, _client("시간B"), "CF-B")
        _journals(submitted, 5)
        _journals(pending, 6)
        _submit(submitted)

        progress = get_counselor_training_progress(counselor)
        context = build_counselor_training_certificate_context(counselor)

        self.assertEqual(progress["journal_completed_count"], 11)
        self.assertEqual(
            progress["counseling_completed_minutes"],
            11 * TRAINING_CREDIT_MINUTES_PER_SESSION,
        )
        self.assertEqual(_bucket_minutes(context, "counseling"), 5 * TRAINING_CREDIT_MINUTES_PER_SESSION)
        self.assertEqual(context["rows"][1][1], "5시간")

    def test_intake_includes_only_submitted_cases(self):
        counselor = _counselor("면접필터")
        submitted = _case(counselor, _client("면접A"), "IN-A", CounselingMethod.REMOTE)
        pending = _case(counselor, _client("면접B"), "IN-B", CounselingMethod.REMOTE)
        _intake(submitted, method=CounselingMethod.IN_PERSON)
        _intake(pending, method=CounselingMethod.REMOTE)
        _submit(submitted)

        progress = get_counselor_training_progress(counselor)
        context = build_counselor_training_certificate_context(counselor)

        self.assertEqual(progress["intake_completed_count"], 2)
        self.assertEqual(progress["intake_in_person_minutes"], 60)
        self.assertEqual(progress["intake_remote_minutes"], 60)
        self.assertEqual(_bucket_minutes(context, "intake"), 60)
        self.assertEqual(context["progress"]["intake_in_person_minutes"], 60)
        self.assertEqual(context["progress"]["intake_remote_minutes"], 0)
        self.assertEqual(context["rows"][0], ["접수면접", "1시간", "대면", ""])

    def test_five_plus_cases_include_only_submitted_cases(self):
        counselor = _counselor("오회필터")
        submitted_long = _case(counselor, _client("오회A"), "FP-A")
        pending_long = _case(counselor, _client("오회B"), "FP-B")
        submitted_short = _case(counselor, _client("오회C"), "FP-C")
        _journals(submitted_long, 5)
        _journals(pending_long, 8)
        _journals(submitted_short, 4)
        _submit(submitted_long)
        _submit(submitted_short)

        context = build_counselor_training_certificate_context(counselor)
        self.assertEqual(context["counseling_cases_5plus_count"], 1)
        self.assertEqual(context["rows"][1][3], "5회기 이상 지속된 사례 총 1건")

    def test_supervision_ignores_submission_and_keeps_legacy_remote(self):
        counselor = _counselor("수퍼유지", cohort=21)
        pending = _case(counselor, _client("수퍼내담"), "SUP-1")
        _journals(pending, 3)
        session = _session(21, 1, SupervisionSessionStatus.COMPLETED, 180)
        self.assertIsNone(session.counseling_method)
        _present(session, counselor)

        progress = get_counselor_training_progress(counselor)
        context = build_counselor_training_certificate_context(counselor)
        self.assertEqual(progress["supervision_remote_minutes"], 180)
        self.assertEqual(context["progress"]["supervision_remote_minutes"], 180)
        self.assertEqual(context["progress"]["supervision_in_person_minutes"], 0)
        self.assertEqual(_bucket_minutes(context, "counseling"), 0)
        self.assertIn(["수퍼비전", "3시간", "비대면", ""], context["rows"])

    def test_case_report_policy_is_not_filtered_by_submission(self):
        counselor = _counselor("보고서유지", cohort=22)
        first = _case(counselor, _client("보고A"), "RPT-A")
        second = _case(counselor, _client("보고B"), "RPT-B")
        _report(counselor, "연결", case=first, cohort=22)
        _report(counselor, "레거시", case=None, cohort=22)

        progress = get_counselor_training_progress(counselor)
        context = build_counselor_training_certificate_context(counselor)
        self.assertEqual(progress["case_report_submitted_count"], 2)
        self.assertEqual(context["progress"]["case_report_submitted_count"], 2)
        self.assertEqual(context["rows"][3][1], "2사례")
        self.assertIsNone(first.records_submitted_at)
        self.assertIsNone(second.records_submitted_at)

    def test_certificate_date_policy_is_unchanged_and_part_of_the_hash(self):
        counselor = _counselor("날짜유지", cohort=23)
        case = _case(counselor, _client("날짜내담"), "DATE-1")
        _submit(case)
        when = timezone.make_aware(datetime(2026, 10, 26, 10, 0))
        _session(23, 1, SupervisionSessionStatus.SCHEDULED, 50, when=when)
        context = build_counselor_training_certificate_context(counselor)
        self.assertEqual(certificate_date_for_counselor(counselor).isoformat(), "2026-10-26")
        self.assertEqual(context["issued_on_label"], format_certificate_date(certificate_date_for_counselor(counselor)))
        before = calculate_training_certificate_hash(counselor, context)

        later = timezone.make_aware(datetime(2026, 11, 9, 10, 0))
        _session(23, 2, SupervisionSessionStatus.SCHEDULED, 50, when=later)
        changed = build_counselor_training_certificate_context(counselor)
        self.assertEqual(changed["issued_on_label"], "2026년 11월 9일")
        self.assertNotEqual(calculate_training_certificate_hash(counselor, changed), before)
        self.assertEqual(_bucket_minutes(changed, "supervision"), 0)


class CertificateApprovalFlowTests(TestCase):
    def setUp(self):
        self.counselor = _counselor("승인대상", cohort=31)
        self.other = _counselor("다른상담", cohort=32)
        self.admin = _user("승인관리@cert.example.com", "홍길동", UserRole.ADMIN)
        self.supervisor = _supervisor("승인수퍼@cert.example.com", "김수퍼", [31])
        self.outsider = _supervisor("타기수@cert.example.com", "박수퍼", [32])
        self.case = _case(self.counselor, _client("승인내담"), "AP-1")
        _journals(self.case, 5)
        _submit(self.case)
        self.counselor_pdf = reverse("counselor:training_certificate")
        self.admin_pdf = reverse(
            "admin_panel:counselor_training_certificate",
            args=[self.counselor.pk],
        )
        self.supervisor_pdf = reverse(
            "supervisor:counselor_training_certificate",
            kwargs={"counselor_id": self.counselor.pk},
        )
        self.admin_approve = reverse(
            "admin_panel:counselor_training_certificate_approve",
            kwargs={"counselor_id": self.counselor.pk},
        )
        self.supervisor_approve = reverse(
            "supervisor:counselor_training_certificate_approve",
            kwargs={"counselor_id": self.counselor.pk},
        )
        self.admin_detail = reverse(
            "admin_panel:counselor_training_progress_detail",
            kwargs={"counselor_id": self.counselor.pk},
        )
        self.supervisor_detail = reverse(
            "supervisor:counselor_training_progress_detail",
            kwargs={"counselor_id": self.counselor.pk},
        )

    def test_missing_approval_is_unapproved_and_pdf_is_blocked(self):
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["status"], STATUS_UNAPPROVED)
        self.assertEqual(status["label"], "미승인")
        self.assertFalse(status["can_issue"])
        self.assertEqual(TrainingCertificateApproval.objects.count(), 0)
        for user, url in (
            (self.counselor, self.counselor_pdf),
            (self.admin, self.admin_pdf),
            (self.supervisor, self.supervisor_pdf),
        ):
            _assert_blocked(self, _http(user).get(url))

    def test_admin_can_approve_any_cohort_and_then_issue_pdf(self):
        response = _http(self.admin).post(self.admin_approve, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "승인완료")
        self.assertContains(response, "홍길동")
        approval = TrainingCertificateApproval.objects.get(counselor=self.counselor)
        self.assertEqual(approval.approved_by, self.admin)
        self.assertEqual(approval.approved_role, UserRole.ADMIN)
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["status"], STATUS_APPROVED)
        self.assertEqual(status["content_hash"], approval.content_hash)
        pdf = _http(self.admin).get(self.admin_pdf)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_supervisor_approves_assigned_cohort_only(self):
        ok = _http(self.supervisor).post(self.supervisor_approve)
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_APPROVED,
        )
        self.assertEqual(_http(self.supervisor).get(self.supervisor_pdf).status_code, 200)

        other_case = _case(self.other, _client("타기수내담"), "AP-OTHER")
        _submit(other_case)
        other_approve = reverse(
            "supervisor:counselor_training_certificate_approve",
            kwargs={"counselor_id": self.other.pk},
        )
        other_pdf = reverse(
            "supervisor:counselor_training_certificate",
            kwargs={"counselor_id": self.other.pk},
        )
        denied = _http(self.supervisor).post(other_approve)
        self.assertEqual(denied.status_code, 403)
        _assert_blocked(self, _http(self.supervisor).get(other_pdf))
        self.assertFalse(
            TrainingCertificateApproval.objects.filter(counselor=self.other).exists()
        )
        outsider_pdf = _http(self.outsider).get(self.supervisor_pdf)
        self.assertEqual(outsider_pdf.status_code, 403)

    def test_counselor_cannot_approve(self):
        before = TrainingCertificateApproval.objects.count()
        self.assertEqual(_http(self.counselor).post(self.admin_approve).status_code, 403)
        self.assertEqual(_http(self.counselor).post(self.supervisor_approve).status_code, 403)
        self.assertEqual(_http(self.counselor).get(self.admin_approve).status_code, 403)
        self.assertEqual(TrainingCertificateApproval.objects.count(), before)
        other_pdf = reverse(
            "admin_panel:counselor_training_certificate",
            args=[self.other.pk],
        )
        self.assertEqual(_http(self.counselor).get(other_pdf).status_code, 403)

    def test_get_does_not_approve_and_post_requires_csrf(self):
        self.assertEqual(_http(self.admin).get(self.admin_approve).status_code, 405)
        self.assertEqual(TrainingCertificateApproval.objects.count(), 0)

        http = HttpClient(enforce_csrf_checks=True)
        http.force_login(self.admin)
        self.assertEqual(http.post(self.admin_approve).status_code, 403)
        self.assertEqual(TrainingCertificateApproval.objects.count(), 0)
        detail = http.get(self.admin_detail)
        token = detail.cookies["csrftoken"].value
        approved = http.post(
            self.admin_approve,
            {"csrfmiddlewaretoken": token},
        )
        self.assertEqual(approved.status_code, 302)
        self.assertEqual(TrainingCertificateApproval.objects.count(), 1)

    def test_zero_submitted_cases_cannot_be_approved(self):
        empty = _counselor("제출없음", cohort=33)
        url = reverse(
            "admin_panel:counselor_training_certificate_approve",
            kwargs={"counselor_id": empty.pk},
        )
        response = _http(self.admin).post(url, follow=True)
        self.assertContains(response, MESSAGE_APPROVAL_REQUIRES_SUBMITTED_CASE)
        self.assertFalse(
            TrainingCertificateApproval.objects.filter(counselor=empty).exists()
        )
        self.assertEqual(
            get_training_certificate_approval_status(empty)["status"],
            STATUS_UNAPPROVED,
        )

    def test_new_submitted_case_requires_reapproval_before_pdf(self):
        approve_training_certificate(self.counselor, self.supervisor)
        first = TrainingCertificateApproval.objects.get(counselor=self.counselor)
        self.assertEqual(_http(self.counselor).get(self.counselor_pdf).status_code, 200)
        self.assertEqual(
            _bucket_minutes(
                build_counselor_training_certificate_context(self.counselor),
                "counseling",
            ),
            5 * TRAINING_CREDIT_MINUTES_PER_SESSION,
        )

        extra = _case(self.counselor, _client("추가내담"), "AP-2")
        _journals(extra, 6)
        _submit(extra)
        current = build_counselor_training_certificate_context(self.counselor)
        self.assertEqual(
            _bucket_minutes(current, "counseling"),
            11 * TRAINING_CREDIT_MINUTES_PER_SESSION,
        )
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["status"], STATUS_REAPPROVAL_REQUIRED)
        self.assertNotEqual(status["content_hash"], first.content_hash)
        _assert_blocked(self, _http(self.counselor).get(self.counselor_pdf))
        _assert_blocked(self, _http(self.admin).get(self.admin_pdf))
        _assert_blocked(self, _http(self.supervisor).get(self.supervisor_pdf))

        _http(self.supervisor).post(self.supervisor_approve)
        self.assertEqual(TrainingCertificateApproval.objects.filter(counselor=self.counselor).count(), 2)
        self.assertTrue(TrainingCertificateApproval.objects.filter(pk=first.pk).exists())
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_APPROVED,
        )
        restored = _http(self.counselor).get(self.counselor_pdf)
        self.assertEqual(restored.status_code, 200)
        self.assertTrue(restored.content.startswith(b"%PDF"))

    def test_supervision_change_requires_reapproval(self):
        approve_training_certificate(self.counselor, self.admin)
        approved_hash = TrainingCertificateApproval.objects.get().content_hash
        session = _session(31, 1, SupervisionSessionStatus.COMPLETED, 120)
        _present(session, self.counselor)
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["status"], STATUS_REAPPROVAL_REQUIRED)
        self.assertNotEqual(status["content_hash"], approved_hash)
        self.assertEqual(status["context"]["progress"]["supervision_remote_minutes"], 120)
        _assert_blocked(self, _http(self.counselor).get(self.counselor_pdf))

    def test_case_report_change_requires_reapproval(self):
        approve_training_certificate(self.counselor, self.admin)
        approved_hash = TrainingCertificateApproval.objects.get().content_hash
        _report(self.counselor, "추가보고서", case=self.case, cohort=31)
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["context"]["progress"]["case_report_submitted_count"], 1)
        self.assertEqual(status["status"], STATUS_REAPPROVAL_REQUIRED)
        self.assertNotEqual(status["content_hash"], approved_hash)
        _assert_blocked(self, _http(self.admin).get(self.admin_pdf))

    def test_same_certificate_context_keeps_the_approval(self):
        approve_training_certificate(self.counselor, self.admin)
        approved_hash = calculate_training_certificate_hash(
            self.counselor,
            build_counselor_training_certificate_context(self.counselor),
        )
        self.counselor.phone = "010-0000-0000"
        self.counselor.save(update_fields=["phone", "updated_at"])
        Case.objects.filter(pk=self.case.pk).update(total_sessions=12)
        _journals(self.case, 2, draft=True)
        untouched = _case(self.counselor, _client("미제출"), "AP-DRAFT")
        _journals(untouched, 6)
        again = calculate_training_certificate_hash(
            self.counselor,
            build_counselor_training_certificate_context(self.counselor),
        )
        self.assertEqual(again, approved_hash)
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_APPROVED,
        )
        self.assertEqual(_http(self.counselor).get(self.counselor_pdf).status_code, 200)
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["journal_completed_count"], 11)

    def test_three_roles_receive_the_same_certificate_context(self):
        approve_training_certificate(self.counselor, self.admin)
        captured = []

        def spy(context):
            captured.append(context)
            return b"%PDF-1.4"

        with patch(
            "apps.counseling.training_certificate_pdf.build_training_certificate_pdf",
            side_effect=spy,
        ):
            self.assertEqual(_http(self.admin).get(self.admin_pdf).status_code, 200)
            self.assertEqual(_http(self.supervisor).get(self.supervisor_pdf).status_code, 200)
            self.assertEqual(_http(self.counselor).get(self.counselor_pdf).status_code, 200)

        self.assertEqual(len(captured), 3)
        stored = TrainingCertificateApproval.objects.get().content_hash
        for context in captured:
            self.assertEqual(context["rows"], captured[0]["rows"])
            self.assertEqual(context["identity"], captured[0]["identity"])
            self.assertEqual(context["issued_on_label"], captured[0]["issued_on_label"])
            self.assertEqual(calculate_training_certificate_hash(self.counselor, context), stored)

    def test_training_progress_screens_keep_unsubmitted_cases(self):
        pending = _case(self.counselor, _client("진행내담"), "AP-OPEN")
        _journals(pending, 6)
        _intake(pending, method=CounselingMethod.REMOTE)
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["journal_completed_count"], 11)
        self.assertEqual(progress["intake_completed_count"], 1)

        admin_page = _http(self.admin).get(self.admin_detail)
        supervisor_page = _http(self.supervisor).get(self.supervisor_detail)
        self.assertEqual(admin_page.context["training_progress"], progress)
        self.assertEqual(supervisor_page.context["training_progress"], progress)
        certificate = build_counselor_training_certificate_context(self.counselor)
        self.assertEqual(_bucket_minutes(certificate, "counseling"), 5 * TRAINING_CREDIT_MINUTES_PER_SESSION)
        self.assertEqual(_bucket_minutes(certificate, "intake"), 0)
        self.assertContains(admin_page, "확인증 반영 최종 제출 사례: 1건")
        self.assertContains(supervisor_page, "확인증 반영 최종 제출 사례: 1건")

    def test_detail_cards_follow_approval_state(self):
        admin_page = _http(self.admin).get(self.admin_detail)
        body = admin_page.content.decode()
        self.assertContains(admin_page, "상태:")
        self.assertContains(admin_page, "미승인")
        self.assertContains(admin_page, "수련확인증 승인")
        self.assertContains(admin_page, "확인증 반영 최종 제출 사례: 1건")
        self.assertNotIn(f'href="{self.admin_pdf}"', body)

        _http(self.supervisor).post(self.supervisor_approve)
        approved = _http(self.supervisor).get(self.supervisor_detail)
        approved_body = approved.content.decode()
        self.assertContains(approved, "승인완료")
        self.assertContains(approved, "김수퍼")
        self.assertContains(approved, "수련확인증 출력")
        self.assertIn(f'href="{self.supervisor_pdf}"', approved_body)
        self.assertNotContains(approved, "수련확인증 재승인")

        _journals(self.case, 1)
        again = _http(self.admin).get(self.admin_detail)
        again_body = again.content.decode()
        self.assertContains(again, "재승인 필요")
        self.assertContains(again, "수련확인증 재승인")
        self.assertNotIn(f'href="{self.admin_pdf}"', again_body)

    def test_dashboard_messages_match_issue_state(self):
        fresh = _counselor("안내상담", cohort=34)
        dashboard = reverse("counselor:dashboard")
        blocked = _http(fresh).get(dashboard)
        self.assertContains(blocked, "수련확인증 출력")
        self.assertContains(blocked, MESSAGE_NO_SUBMITTED_CASE)
        self.assertNotContains(blocked, f'href="{reverse("counselor:training_certificate")}"')

        case = _case(fresh, _client("안내내담"), "MSG-1")
        _journals(case, 1)
        _submit(case)
        waiting = _http(fresh).get(dashboard)
        self.assertContains(waiting, MESSAGE_UNAPPROVED)
        self.assertNotContains(waiting, MESSAGE_NO_SUBMITTED_CASE)

        approve_training_certificate(fresh, self.admin)
        ready = _http(fresh).get(dashboard)
        self.assertContains(ready, f'href="{reverse("counselor:training_certificate")}"')
        self.assertNotContains(ready, MESSAGE_UNAPPROVED)

        _report(fresh, "변경보고서", case=case, cohort=34)
        changed = _http(fresh).get(dashboard)
        self.assertContains(changed, MESSAGE_REAPPROVAL)
        self.assertNotContains(changed, f'href="{reverse("counselor:training_certificate")}"')

    def test_final_submit_does_not_create_an_approval(self):
        counselor = _counselor("종결유지", cohort=35)
        case = _case(counselor, _client("종결내담"), "CLOSE-1")
        _journals(case, 1)
        TerminationCounselingRecord.objects.create(
            case=case,
            counselor=counselor,
            is_draft=False,
        )
        ok, errors = submit_case_records(case, counselor)
        self.assertTrue(ok, errors)
        case.refresh_from_db()
        self.assertEqual(case.status, CaseStatus.CLOSED)
        self.assertEqual(case.termination_type, "NORMAL")
        self.assertIsNotNone(case.closed_at)
        self.assertIsNotNone(case.records_submitted_at)
        self.assertFalse(
            TrainingCertificateApproval.objects.filter(counselor=counselor).exists()
        )


class CertificateHashStabilityTests(TestCase):
    def test_repeated_hash_of_the_same_context_matches(self):
        counselor = _counselor("해시동일", cohort=41)
        case = _case(counselor, _client("해시내담"), "HASH-1")
        _journals(case, 2, method=CounselingMethod.REMOTE)
        _submit(case)
        first = build_counselor_training_certificate_context(counselor)
        second = build_counselor_training_certificate_context(counselor)
        self.assertEqual(
            calculate_training_certificate_hash(counselor, first),
            calculate_training_certificate_hash(counselor, second),
        )
        self.assertEqual(first["rows"], second["rows"])
