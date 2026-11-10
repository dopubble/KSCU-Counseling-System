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
    CasePresentationComment,
    CasePresentationPost,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
    SupervisionAttendance,
    SupervisionAttendanceStatus,
    SupervisionSession,
    SupervisionSessionStatus,
    TrainingCertificateApproval,
    TrainingCertificateApprovalRevocation,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
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
    build_training_certificate_approval_payload,
    calculate_training_certificate_hash,
    certificate_date_for_counselor,
    get_training_certificate_approval_status,
    legacy_display_approval_hash,
    revoke_training_certificate,
    legacy_display_approval_payload,
    legacy_metrics_approval_hash,
)
from apps.counseling.training_certificate_pdf import format_certificate_date
from apps.counseling.training_progress import (
    TRAINING_CREDIT_MINUTES_PER_SESSION,
    get_counselor_training_progress,
)
from apps.documents.models import ConsentDocType, ConsentDocument
from apps.sessions_app.models import CounselingJournal, TerminationCounselingRecord


def _status_line(label, assigned, submitted):
    return (
        f'상태: <span class="fw-semibold">{label}</span> '
        f"(총 {assigned}개 사례 중 최종 종결 처리 {submitted}건)"
    )


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
    if cohort is not None:
        TrainingEnrollment.objects.create(
            counselor=user,
            cohort=cohort,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
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
        training_enrollment=(
            TrainingEnrollment.objects.filter(counselor=counselor).get()
            if TrainingEnrollment.objects.filter(counselor=counselor).count() == 1
            else None
        ),
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
        self.assertEqual(context["rows"][1][3], "5회기 이상 지속된 사례 총 1건 포함")

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
        self.assertIn(
            ["집단 수퍼비전", "3시간", "비대면", "내 사례로 슈퍼비전 받은시간 : 0시간"],
            context["rows"],
        )

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
        self.assertNotIn("사례보고서", [row[0] for row in context["rows"]])
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
        detail = http.get(self.admin_detail, follow=True)
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

    def test_submission_line_counts_assigned_cases_without_changing_approval(self):
        counselor = _counselor("부분제출", cohort=36)
        supervisor = _supervisor("부분수퍼@cert.example.com", "부분수퍼", [36])
        pending = _case(counselor, _client("미제출활성"), "PART-1")
        closed_submitted = _case(counselor, _client("종결제출"), "PART-2")
        closed_submitted.status = CaseStatus.CLOSED
        closed_submitted.save(update_fields=["status"])
        _journals(closed_submitted, 2)
        _submit(closed_submitted)
        active_submitted = _case(counselor, _client("활성제출"), "PART-3")
        _journals(active_submitted, 3)
        _submit(active_submitted)

        admin_detail = reverse(
            "admin_panel:counselor_training_progress_detail",
            kwargs={"counselor_id": counselor.pk},
        )
        supervisor_detail = reverse(
            "supervisor:counselor_training_progress_detail",
            kwargs={"counselor_id": counselor.pk},
        )
        partial = _status_line("미승인", 3, 2)
        self.assertContains(_http(self.admin).get(admin_detail, follow=True), partial)
        self.assertContains(_http(supervisor).get(supervisor_detail, follow=True), partial)

        approve_url = reverse(
            "admin_panel:counselor_training_certificate_approve",
            kwargs={"counselor_id": counselor.pk},
        )
        approved = _http(self.admin).post(approve_url)
        self.assertEqual(approved.status_code, 302)
        self.assertEqual(
            get_training_certificate_approval_status(counselor)["status"],
            STATUS_APPROVED,
        )
        self.assertEqual(
            TrainingCertificateApproval.objects.filter(counselor=counselor).count(),
            1,
        )

        _journals(pending, 4)
        _submit(pending)
        again = _http(supervisor).get(supervisor_detail, follow=True)
        self.assertContains(again, _status_line("재승인 필요", 3, 3))
        self.assertNotContains(again, "사례 제출 현황")
        self.assertContains(again, "재승인 필요")
        self.assertEqual(
            get_training_certificate_approval_status(counselor)["status"],
            STATUS_REAPPROVAL_REQUIRED,
        )
        self.assertEqual(
            TrainingCertificateApproval.objects.filter(counselor=counselor).count(),
            1,
        )

        none_submitted = _counselor("영건제출", cohort=37)
        open_case = _case(none_submitted, _client("영건활성"), "ZERO-1")
        closed_case = _case(none_submitted, _client("영건종결"), "ZERO-2")
        closed_case.status = CaseStatus.CLOSED
        closed_case.save(update_fields=["status"])
        self.assertIsNone(open_case.records_submitted_at)
        self.assertIsNone(closed_case.records_submitted_at)
        none_detail = reverse(
            "admin_panel:counselor_training_progress_detail",
            kwargs={"counselor_id": none_submitted.pk},
        )
        self.assertContains(
            _http(self.admin).get(none_detail, follow=True),
            _status_line("미승인", 2, 0),
        )
        blocked = _http(self.admin).post(
            reverse(
                "admin_panel:counselor_training_certificate_approve",
                kwargs={"counselor_id": none_submitted.pk},
            ),
            follow=True,
        )
        self.assertContains(blocked, MESSAGE_APPROVAL_REQUIRES_SUBMITTED_CASE)

        complete = _counselor("전부제출", cohort=38)
        first = _case(complete, _client("전부1"), "ALL-1")
        second = _case(complete, _client("전부2"), "ALL-2")
        second.status = CaseStatus.CLOSED
        second.save(update_fields=["status"])
        _submit(first)
        _submit(second)
        complete_detail = reverse(
            "supervisor:counselor_training_progress_detail",
            kwargs={"counselor_id": complete.pk},
        )
        complete_supervisor = _supervisor("전부수퍼@cert.example.com", "전부수퍼", [38])
        self.assertContains(
            _http(complete_supervisor).get(complete_detail, follow=True),
            _status_line("미승인", 2, 2),
        )

    def test_status_line_combines_label_and_case_counts(self):
        counselor = _counselor("한줄현황", cohort=39)
        supervisor = _supervisor("한줄수퍼@cert.example.com", "한줄수퍼", [39])
        cases = [
            _case(counselor, _client(f"한줄{number}"), f"LINE-{number}")
            for number in range(1, 5)
        ]
        cases[0].status = CaseStatus.CLOSED
        cases[0].save(update_fields=["status"])
        admin_detail = reverse(
            "admin_panel:counselor_training_progress_detail",
            kwargs={"counselor_id": counselor.pk},
        )
        supervisor_detail = reverse(
            "supervisor:counselor_training_progress_detail",
            kwargs={"counselor_id": counselor.pk},
        )
        admin_page = _http(self.admin).get(admin_detail, follow=True)
        supervisor_page = _http(supervisor).get(supervisor_detail, follow=True)
        self.assertContains(admin_page, _status_line("미승인", 4, 0))
        self.assertContains(supervisor_page, _status_line("미승인", 4, 0))
        self.assertNotContains(admin_page, "사례 제출 현황")
        self.assertNotContains(supervisor_page, "사례 제출 현황")

        blocked = _http(self.admin).post(
            reverse(
                "admin_panel:counselor_training_certificate_approve",
                kwargs={"counselor_id": counselor.pk},
            ),
            follow=True,
        )
        self.assertContains(blocked, MESSAGE_APPROVAL_REQUIRES_SUBMITTED_CASE)

        for case in cases[2:]:
            _journals(case, 2)
            _submit(case)
        self.assertContains(
            _http(self.admin).get(admin_detail, follow=True),
            _status_line("미승인", 4, 2),
        )
        self.assertContains(
            _http(supervisor).get(supervisor_detail, follow=True),
            _status_line("미승인", 4, 2),
        )
        approved = _http(self.admin).post(
            reverse(
                "admin_panel:counselor_training_certificate_approve",
                kwargs={"counselor_id": counselor.pk},
            )
        )
        self.assertEqual(approved.status_code, 302)
        self.assertContains(
            _http(self.admin).get(admin_detail, follow=True),
            _status_line("승인완료", 4, 2),
        )
        self.assertContains(
            _http(supervisor).get(supervisor_detail, follow=True),
            _status_line("승인완료", 4, 2),
        )

        _journals(cases[1], 3)
        _submit(cases[1])
        again = _http(supervisor).get(supervisor_detail, follow=True)
        self.assertContains(again, _status_line("재승인 필요", 4, 3))
        self.assertNotContains(again, "사례 제출 현황")
        self.assertEqual(
            get_training_certificate_approval_status(counselor)["status"],
            STATUS_REAPPROVAL_REQUIRED,
        )
        self.assertEqual(
            TrainingCertificateApproval.objects.filter(counselor=counselor).count(),
            1,
        )
        pdf = _http(counselor).get(reverse("counselor:training_certificate"))
        self.assertEqual(pdf.status_code, 403)

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
        self.assertEqual(status["context"]["progress"]["supervision_remote_minutes"], 180)
        _assert_blocked(self, _http(self.counselor).get(self.counselor_pdf))

    def test_v1_metrics_hash_requires_reapproval_until_v2_is_stored(self):
        approve_training_certificate(self.counselor, self.admin)
        approval = TrainingCertificateApproval.objects.get(counselor=self.counselor)
        context = build_counselor_training_certificate_context(self.counselor)
        stored = legacy_metrics_approval_hash(self.counselor, context)
        approval.content_hash = stored
        approval.save(update_fields=["content_hash"])
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(context["own_case_supervision_posts"], 0)
        self.assertEqual(
            build_training_certificate_approval_payload(self.counselor, context)["schema"],
            "certificate-metrics-v4",
        )
        self.assertEqual(status["status"], STATUS_REAPPROVAL_REQUIRED)
        self.assertNotEqual(status["content_hash"], stored)
        _assert_blocked(self, _http(self.counselor).get(self.counselor_pdf))

        _report(self.counselor, "승인후추가", case=self.case, cohort=31)
        later = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(later["context"]["own_case_supervision_posts"], 1)
        self.assertIn(
            "내 사례로 슈퍼비전 받은시간 : 0시간",
            [row[3] for row in later["context"]["rows"]],
        )
        self.assertEqual(later["status"], STATUS_REAPPROVAL_REQUIRED)
        approval.refresh_from_db()
        self.assertEqual(approval.content_hash, stored)

        _http(self.admin).post(self.admin_approve)
        self.assertEqual(
            TrainingCertificateApproval.objects.filter(counselor=self.counselor).count(),
            2,
        )
        approval.refresh_from_db()
        self.assertEqual(approval.content_hash, stored)
        renewed = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(renewed["status"], STATUS_APPROVED)
        self.assertEqual(renewed["content_hash"], renewed["latest"].content_hash)
        self.assertNotEqual(renewed["latest"].pk, approval.pk)
        self.assertEqual(
            build_training_certificate_approval_payload(
                self.counselor,
                renewed["context"],
            )["schema"],
            "certificate-metrics-v4",
        )
        issued = _http(self.counselor).get(self.counselor_pdf)
        self.assertEqual(issued.status_code, 200)
        self.assertTrue(issued.content.startswith(b"%PDF"))

    def test_extra_post_beyond_attendance_keeps_the_credited_approval(self):
        session = _session(31, 1, SupervisionSessionStatus.COMPLETED, 120)
        _present(session, self.counselor)
        _report(self.counselor, "내보고서", case=self.case, cohort=31)
        approve_training_certificate(self.counselor, self.admin)
        approved = TrainingCertificateApproval.objects.get(counselor=self.counselor)
        before = build_counselor_training_certificate_context(self.counselor)
        self.assertEqual(before["own_case_supervision_posts"], 1)
        self.assertEqual(before["progress"]["case_report_submitted_count"], 1)
        self.assertEqual(before["progress"]["supervision_remote_minutes"], 90)
        self.assertIn(
            "내 사례로 슈퍼비전 받은시간 : 2시간",
            [row[3] for row in before["rows"]],
        )

        _report(self.counselor, "같은사례추가", case=self.case, cohort=31)
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["context"]["own_case_supervision_posts"], 2)
        self.assertEqual(status["context"]["progress"]["case_report_submitted_count"], 1)
        self.assertEqual(status["context"]["progress"]["supervision_remote_minutes"], 90)
        self.assertEqual(
            status["context"]["progress"]["supervision_in_person_minutes"],
            0,
        )
        self.assertIn(
            ["집단 수퍼비전", "1시간 30분", "비대면", "내 사례로 슈퍼비전 받은시간 : 2시간"],
            status["context"]["rows"],
        )
        self.assertIn(
            ["개인 수퍼비전", "2시간", "—", ""],
            status["context"]["rows"],
        )
        self.assertNotIn(
            "own_case_supervision_posts",
            build_training_certificate_approval_payload(self.counselor, status["context"]),
        )
        self.assertEqual(status["status"], STATUS_APPROVED)
        self.assertEqual(status["content_hash"], approved.content_hash)
        self.assertEqual(_http(self.counselor).get(self.counselor_pdf).status_code, 200)

    def test_comments_after_v2_approval_keep_the_approval(self):
        post = _report(self.counselor, "댓글대상", case=self.case, cohort=31)
        approve_training_certificate(self.counselor, self.admin)
        approved_hash = TrainingCertificateApproval.objects.get().content_hash
        for index in range(5):
            CasePresentationComment.objects.create(
                post=post,
                author=self.counselor,
                content=f"댓글{index}",
            )
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["context"]["own_case_supervision_posts"], 1)
        self.assertIn(
            ["집단 수퍼비전", "0분", "—", "내 사례로 슈퍼비전 받은시간 : 0시간"],
            status["context"]["rows"],
        )
        self.assertIn(["개인 수퍼비전", "0시간", "—", ""], status["context"]["rows"])
        self.assertEqual(status["content_hash"], approved_hash)
        self.assertEqual(status["status"], STATUS_APPROVED)
        self.assertEqual(_http(self.counselor).get(self.counselor_pdf).status_code, 200)

    def test_other_counselor_post_after_v2_approval_keeps_the_approval(self):
        approve_training_certificate(self.counselor, self.admin)
        approved_hash = TrainingCertificateApproval.objects.get(
            counselor=self.counselor
        ).content_hash
        _report(self.other, "타인보고서", case=None, cohort=32)
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["context"]["own_case_supervision_posts"], 0)
        self.assertEqual(
            status["context"]["rows"][2][3],
            "내 사례로 슈퍼비전 받은시간 : 0시간",
        )
        self.assertEqual(status["content_hash"], approved_hash)
        self.assertEqual(status["status"], STATUS_APPROVED)
        self.assertEqual(_http(self.counselor).get(self.counselor_pdf).status_code, 200)

    def test_case_report_change_requires_reapproval(self):
        approve_training_certificate(self.counselor, self.admin)
        approved_hash = TrainingCertificateApproval.objects.get().content_hash
        _report(self.counselor, "추가보고서", case=self.case, cohort=31)
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["context"]["progress"]["case_report_submitted_count"], 1)
        self.assertEqual(status["status"], STATUS_REAPPROVAL_REQUIRED)
        self.assertNotEqual(status["content_hash"], approved_hash)
        _assert_blocked(self, _http(self.admin).get(self.admin_pdf))

    def test_legacy_display_hash_requires_reapproval(self):
        approve_training_certificate(self.counselor, self.admin)
        approval = TrainingCertificateApproval.objects.get(counselor=self.counselor)
        context = build_counselor_training_certificate_context(self.counselor)
        count = context["counseling_cases_5plus_count"]
        old_remark = f"5회기 이상 지속된 사례 총 {count}건"
        approval.content_hash = legacy_display_approval_hash(
            self.counselor,
            context,
            old_remark,
        )
        approval.save(update_fields=["content_hash"])
        legacy = legacy_display_approval_payload(self.counselor, context, old_remark)
        current_rows = [list(row) for row in context["rows"]]
        for row in current_rows:
            if row[3].endswith(" 포함"):
                row[3] = row[3].removesuffix(" 포함")
            if row[3].startswith("내 사례로 슈퍼비전 받은시간"):
                row[3] = ""
        self.assertEqual(legacy["rows"][-1], ["사례보고서", "0사례", "비대면", ""])
        self.assertIn("수퍼비전", [row[0] for row in legacy["rows"]])
        self.assertIn("집단 수퍼비전", [row[0] for row in context["rows"]])
        self.assertIn("개인 수퍼비전", [row[0] for row in context["rows"]])
        self.assertNotIn("사례보고서", [row[0] for row in context["rows"]])
        self.assertIn(
            "내 사례로 슈퍼비전 받은시간 : 0시간",
            [row[3] for row in context["rows"]],
        )
        displayed = [
            [label, value]
            for label, value in context["identity"]
            if label != "수퍼바이저"
        ]
        self.assertEqual(
            legacy["identity"],
            displayed + [["수퍼바이저", context["supervisor_label"]]],
        )
        self.assertNotIn("(인)", context["supervisor_label"])
        self.assertEqual(legacy["issued_on"], context["issued_on_label"])
        self.assertEqual(legacy["cases_5plus"], count)
        self.assertIn("intake_minutes", legacy)
        self.assertNotIn("schema", legacy)

        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["status"], STATUS_REAPPROVAL_REQUIRED)
        self.assertNotEqual(status["content_hash"], approval.content_hash)
        self.assertTrue(any(cell.endswith("건 포함") for row in status["context"]["rows"] for cell in row))
        _assert_blocked(self, _http(self.counselor).get(self.counselor_pdf))
        approval.refresh_from_db()
        self.assertEqual(approval.content_hash, legacy_display_approval_hash(
            self.counselor,
            context,
            old_remark,
        ))
        self.assertEqual(TrainingCertificateApproval.objects.filter(counselor=self.counselor).count(), 1)

        extra = _case(self.counselor, _client("실적변경"), "AP-LEGACY")
        _journals(extra, 6)
        _submit(extra)
        changed = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(changed["status"], STATUS_REAPPROVAL_REQUIRED)
        self.assertNotEqual(changed["content_hash"], approval.content_hash)
        _assert_blocked(self, _http(self.counselor).get(self.counselor_pdf))
        _assert_blocked(self, _http(self.admin).get(self.admin_pdf))
        _assert_blocked(self, _http(self.supervisor).get(self.supervisor_pdf))

        stored = approval.content_hash
        _http(self.admin).post(self.admin_approve)
        self.assertEqual(TrainingCertificateApproval.objects.filter(counselor=self.counselor).count(), 2)
        approval.refresh_from_db()
        self.assertEqual(approval.content_hash, stored)
        renewed = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(renewed["status"], STATUS_APPROVED)
        self.assertEqual(renewed["content_hash"], renewed["latest"].content_hash)
        self.assertEqual(
            build_training_certificate_approval_payload(
                self.counselor,
                renewed["context"],
            )["schema"],
            "certificate-metrics-v4",
        )
        restored = _http(self.counselor).get(self.counselor_pdf)
        self.assertEqual(restored.status_code, 200)
        self.assertTrue(restored.content.startswith(b"%PDF"))

    def test_same_total_minutes_with_method_split_requires_reapproval(self):
        counselor = _counselor("방식분할", cohort=31)
        case = _case(counselor, _client("방식내담"), "SPLIT-1")
        _journals(case, 10, method=CounselingMethod.IN_PERSON)
        _submit(case)
        approve_training_certificate(counselor, self.admin)
        approved = TrainingCertificateApproval.objects.get(counselor=counselor)
        context = build_counselor_training_certificate_context(counselor)
        payload = build_training_certificate_approval_payload(counselor, context)
        self.assertEqual(
            set(payload),
            {
                "schema",
                "counselor_id",
                "name",
                "birth_date",
                "supervisor",
                "issued_on",
                "cases_5plus",
                "case_report_count",
                "intake_minutes",
                "counseling_minutes",
                "supervision_minutes",
                "group_supervision_minutes",
                "group_supervision_method",
                "individual_supervision_minutes",
            },
        )
        self.assertEqual(payload["schema"], "certificate-metrics-v4")
        for bucket in ("intake_minutes", "counseling_minutes", "supervision_minutes"):
            self.assertEqual(
                set(payload[bucket]),
                {"in_person", "remote", "unknown"},
            )
        self.assertEqual(
            payload["counseling_minutes"],
            {"in_person": 600, "remote": 0, "unknown": 0},
        )
        self.assertNotIn("rows", payload)
        self.assertEqual(
            get_training_certificate_approval_status(counselor)["status"],
            STATUS_APPROVED,
        )

        flipped = list(case.journals.order_by("session_number")[:5].values_list("pk", flat=True))
        CounselingJournal.objects.filter(pk__in=flipped).update(
            counseling_method=CounselingMethod.REMOTE
        )
        status = get_training_certificate_approval_status(counselor)
        progress = status["context"]["progress"]
        self.assertEqual(progress["counseling_in_person_minutes"], 300)
        self.assertEqual(progress["counseling_remote_minutes"], 300)
        self.assertEqual(
            progress["counseling_in_person_minutes"]
            + progress["counseling_remote_minutes"]
            + progress["counseling_unknown_minutes"],
            600,
        )
        self.assertEqual(status["status"], STATUS_REAPPROVAL_REQUIRED)
        self.assertNotEqual(status["content_hash"], approved.content_hash)
        self.assertEqual(approved.content_hash, TrainingCertificateApproval.objects.get(pk=approved.pk).content_hash)
        _assert_blocked(
            self,
            _http(counselor).get(reverse("counselor:training_certificate")),
        )

    def test_identity_and_intake_changes_require_reapproval(self):
        approve_training_certificate(self.counselor, self.admin)
        approved = TrainingCertificateApproval.objects.get(counselor=self.counselor)
        baseline = approved.content_hash

        def current_hash():
            return calculate_training_certificate_hash(
                self.counselor,
                build_counselor_training_certificate_context(self.counselor),
            )

        self.counselor.name = "변경이름"
        self.counselor.save(update_fields=["name", "updated_at"])
        self.assertNotEqual(current_hash(), baseline)
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_REAPPROVAL_REQUIRED,
        )
        self.counselor.name = "승인대상"
        self.counselor.save(update_fields=["name", "updated_at"])
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_APPROVED,
        )

        profile = self.counselor.counselor_profile
        profile.birth_date = datetime(1991, 4, 8).date()
        profile.save(update_fields=["birth_date", "updated_at"])
        self.assertNotEqual(current_hash(), baseline)
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_REAPPROVAL_REQUIRED,
        )
        profile.birth_date = None
        profile.save(update_fields=["birth_date", "updated_at"])

        self.supervisor.name = "다른수퍼"
        self.supervisor.save(update_fields=["name", "updated_at"])
        self.assertNotEqual(current_hash(), baseline)
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_REAPPROVAL_REQUIRED,
        )
        self.supervisor.name = "김수퍼"
        self.supervisor.save(update_fields=["name", "updated_at"])
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_APPROVED,
        )

        _intake(self.case, method=CounselingMethod.IN_PERSON)
        self.assertEqual(
            build_counselor_training_certificate_context(self.counselor)["progress"][
                "intake_in_person_minutes"
            ],
            60,
        )
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_REAPPROVAL_REQUIRED,
        )
        self.assertEqual(approved.content_hash, baseline)
        _assert_blocked(self, _http(self.counselor).get(self.counselor_pdf))

    def test_signature_layout_keeps_an_existing_approval(self):
        approve_training_certificate(self.counselor, self.admin)
        context = build_counselor_training_certificate_context(self.counselor)
        self.assertEqual(
            context["supervisor_lines"],
            ["김수퍼 교수 (인)"],
        )
        self.assertEqual(context["supervisor_label"], "김수퍼 교수")
        payload = build_training_certificate_approval_payload(self.counselor, context)
        self.assertEqual(payload["schema"], "certificate-metrics-v4")
        self.assertEqual(payload["supervisor"], context["supervisor_label"])
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_APPROVED,
        )
        issued = _http(self.counselor).get(self.counselor_pdf)
        self.assertEqual(issued.status_code, 200)
        self.assertTrue(issued.content.startswith(b"%PDF"))

    def test_include_era_display_hash_requires_reapproval(self):
        approve_training_certificate(self.counselor, self.admin)
        approval = TrainingCertificateApproval.objects.get(counselor=self.counselor)
        context = build_counselor_training_certificate_context(self.counselor)
        remark = (
            f"5회기 이상 지속된 사례 총 {context['counseling_cases_5plus_count']}건 포함"
        )
        approval.content_hash = legacy_display_approval_hash(
            self.counselor,
            context,
            remark,
        )
        approval.save(update_fields=["content_hash"])
        stored = approval.content_hash
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["status"], STATUS_REAPPROVAL_REQUIRED)
        _assert_blocked(self, _http(self.counselor).get(self.counselor_pdf))
        approval.refresh_from_db()
        self.assertEqual(approval.content_hash, stored)

    def test_unknown_approval_hash_is_not_treated_as_current(self):
        approve_training_certificate(self.counselor, self.admin)
        approval = TrainingCertificateApproval.objects.get(counselor=self.counselor)
        approval.content_hash = "c" * 64
        approval.save(update_fields=["content_hash"])
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["status"], STATUS_REAPPROVAL_REQUIRED)
        self.assertFalse(status["can_issue"])
        _assert_blocked(self, _http(self.counselor).get(self.counselor_pdf))

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

        admin_page = _http(self.admin).get(self.admin_detail, follow=True)
        supervisor_page = _http(self.supervisor).get(self.supervisor_detail, follow=True)
        self.assertEqual(admin_page.context["training_progress"], progress)
        self.assertEqual(supervisor_page.context["training_progress"], progress)
        certificate = build_counselor_training_certificate_context(self.counselor)
        self.assertEqual(_bucket_minutes(certificate, "counseling"), 5 * TRAINING_CREDIT_MINUTES_PER_SESSION)
        self.assertEqual(_bucket_minutes(certificate, "intake"), 0)
        self.assertContains(admin_page, _status_line("미승인", 2, 1))
        self.assertContains(supervisor_page, _status_line("미승인", 2, 1))
        self.assertNotContains(admin_page, "사례 제출 현황")

    def test_detail_cards_follow_approval_state(self):
        admin_page = _http(self.admin).get(self.admin_detail, follow=True)
        body = admin_page.content.decode()
        self.assertContains(admin_page, "상태:")
        self.assertContains(admin_page, "미승인")
        self.assertContains(admin_page, "수련확인증 승인")
        self.assertContains(admin_page, _status_line("미승인", 1, 1))
        self.assertNotContains(admin_page, "사례 제출 현황")
        enrollment_pdf = reverse(
            "supervisor:enrollment_training_certificate",
            kwargs={"enrollment_id": self.case.training_enrollment_id},
        )
        self.assertNotIn(f'href="{enrollment_pdf}"', body)
        self.assertNotIn(f'href="{self.admin_pdf}"', body)

        _http(self.supervisor).post(self.supervisor_approve)
        approved = _http(self.supervisor).get(self.supervisor_detail, follow=True)
        approved_body = approved.content.decode()
        self.assertContains(approved, "승인완료")
        self.assertContains(approved, "김수퍼")
        self.assertContains(approved, "수련확인증 출력")
        self.assertIn(f'href="{enrollment_pdf}"', approved_body)
        self.assertNotContains(approved, "수련확인증 재승인")

        _journals(self.case, 1)
        again = _http(self.admin).get(self.admin_detail, follow=True)
        again_body = again.content.decode()
        self.assertContains(again, "재승인 필요")
        self.assertContains(again, "수련확인증 재승인")
        self.assertNotIn(f'href="{enrollment_pdf}"', again_body)
        self.assertNotIn(f'href="{self.admin_pdf}"', again_body)

    def test_dashboard_messages_match_issue_state(self):
        fresh = _counselor("안내상담", cohort=34)
        dashboard = reverse("counselor:dashboard")
        blocked = _http(fresh).get(dashboard)
        self.assertContains(blocked, "수련확인증 출력")
        self.assertNotContains(blocked, "사례 제출 현황")
        self.assertNotContains(blocked, "최종 종결 처리")
        self.assertContains(blocked, "수련확인증 상태")
        self.assertContains(blocked, "미승인")
        self.assertContains(blocked, MESSAGE_NO_SUBMITTED_CASE)
        certificate_href = (
            f'{reverse("counselor:training_certificate")}'
            f'?enrollment={TrainingEnrollment.objects.get(counselor=fresh).pk}'
        )
        self.assertNotContains(blocked, certificate_href)

        case = _case(fresh, _client("안내내담"), "MSG-1")
        _journals(case, 1)
        _submit(case)
        waiting = _http(fresh).get(dashboard)
        self.assertContains(waiting, MESSAGE_UNAPPROVED)
        self.assertContains(waiting, "미승인")
        self.assertNotContains(waiting, "승인완료")
        self.assertNotContains(waiting, "재승인 필요")
        self.assertNotContains(waiting, MESSAGE_NO_SUBMITTED_CASE)

        approve_training_certificate(fresh, self.admin)
        ready = _http(fresh).get(dashboard)
        self.assertContains(ready, f'href="{certificate_href}"')
        self.assertContains(ready, "승인완료")
        self.assertNotContains(ready, "미승인")
        self.assertNotContains(ready, "재승인 필요")
        self.assertNotContains(ready, MESSAGE_UNAPPROVED)

        _report(fresh, "변경보고서", case=case, cohort=34)
        changed = _http(fresh).get(dashboard)
        self.assertContains(changed, MESSAGE_REAPPROVAL)
        self.assertContains(changed, "재승인 필요")
        self.assertNotContains(changed, "승인완료")
        self.assertNotContains(changed, "미승인")
        self.assertNotContains(changed, certificate_href)

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
        dashboard = _http(counselor).get(reverse("counselor:dashboard"))
        body = dashboard.content.decode()
        training, closed = body.split('id="counselor-training-progress"', 1)[1].split(
            'id="counselor-closed-cases"', 1
        )
        self.assertIn("수련확인증 상태", training)
        self.assertIn("미승인", training)
        self.assertNotIn("수련확인증 상태", closed)
        self.assertIn("최종 제출 완료", closed)
        self.assertIn(MESSAGE_UNAPPROVED, body)


class CertificateApprovalRevocationTests(TestCase):
    def setUp(self):
        self.counselor = _counselor("취소대상", cohort=61)
        self.admin = _user("취소관리@cert.example.com", "홍길동", UserRole.ADMIN)
        self.supervisor = _supervisor("취소수퍼@cert.example.com", "김수퍼", [61])
        self.outsider = _supervisor("취소타기수@cert.example.com", "박수퍼", [62])
        self.case = _case(self.counselor, _client("취소내담"), "RV-1")
        _journals(self.case, 5)
        _submit(self.case)
        self.admin_detail = reverse(
            "admin_panel:counselor_training_progress_detail",
            kwargs={"counselor_id": self.counselor.pk},
        )
        self.supervisor_detail = reverse(
            "supervisor:counselor_training_progress_detail",
            kwargs={"counselor_id": self.counselor.pk},
        )
        self.admin_revoke = reverse(
            "admin_panel:counselor_training_certificate_revoke",
            kwargs={"counselor_id": self.counselor.pk},
        )
        self.supervisor_revoke = reverse(
            "supervisor:counselor_training_certificate_revoke",
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
        self.counselor_pdf = reverse("counselor:training_certificate")
        self.admin_pdf = reverse(
            "admin_panel:counselor_training_certificate",
            args=[self.counselor.pk],
        )
        self.supervisor_pdf = reverse(
            "supervisor:counselor_training_certificate",
            kwargs={"counselor_id": self.counselor.pk},
        )

    def _snapshot(self, approval):
        approval.refresh_from_db()
        return {
            "counselor_id": approval.counselor_id,
            "approved_by_id": approval.approved_by_id,
            "approved_role": approval.approved_role,
            "approved_at": approval.approved_at,
            "content_hash": approval.content_hash,
        }

    def test_unapproved_card_has_no_revoke_button(self):
        page = _http(self.admin).get(self.admin_detail, follow=True)
        self.assertContains(page, "미승인")
        self.assertNotContains(page, "승인 취소")
        self.assertNotContains(page, "revokeTrainingCertificateModal")
        denied = _http(self.admin).post(self.admin_revoke)
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(TrainingCertificateApprovalRevocation.objects.count(), 0)

    def test_approved_card_offers_revoke_confirmation(self):
        approve_training_certificate(self.counselor, self.supervisor)
        for user, detail in (
            (self.admin, self.admin_detail),
            (self.supervisor, self.supervisor_detail),
        ):
            page = _http(user).get(detail, follow=True)
            self.assertContains(page, "승인완료")
            self.assertContains(page, ">승인 취소</button>")
            self.assertContains(
                page,
                "수련확인증 승인을 취소하시겠습니까? 승인 취소 즉시 수련확인증 출력이 제한됩니다.",
            )
            self.assertContains(page, ">취소</button>")

    def test_admin_revoke_marks_unapproved_and_blocks_pdf(self):
        approval = approve_training_certificate(self.counselor, self.supervisor)
        before = self._snapshot(approval)
        payload = build_training_certificate_approval_payload(
            self.counselor,
            build_counselor_training_certificate_context(self.counselor),
        )
        response = _http(self.admin).post(self.admin_revoke, follow=True)
        self.assertContains(response, _status_line("미승인", 1, 1))
        self.assertContains(response, "최근 승인 취소: 홍길동 / ")
        self.assertContains(response, "수련확인증 승인")
        self.assertNotContains(response, "revokeTrainingCertificateModal")
        self.assertNotContains(response, "재승인 필요")
        approval.refresh_from_db()
        self.assertEqual(self._snapshot(approval), before)
        revocation = TrainingCertificateApprovalRevocation.objects.get()
        self.assertEqual(revocation.approval_id, approval.pk)
        self.assertEqual(revocation.revoked_by, self.admin)
        self.assertEqual(revocation.revoked_role, UserRole.ADMIN)
        self.assertEqual(revocation.reason, "")
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["status"], STATUS_UNAPPROVED)
        self.assertFalse(status["can_issue"])
        self.assertRegex(status["revoked_at_label"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(status["revoked_by_name"], "홍길동")
        after = build_training_certificate_approval_payload(
            self.counselor,
            status["context"],
        )
        comparable = dict(after)
        expected = dict(payload)
        for item in (comparable, expected):
            item["schema"] = "certificate-metrics-v4"
            item.pop("training_enrollment_id", None)
        self.assertEqual(comparable, expected)
        self.assertEqual(after["schema"], "certificate-metrics-v4")
        self.assertEqual(
            after["training_enrollment_id"],
            str(status["context"]["training_enrollment"].pk),
        )
        self.assertNotIn("revocation", after)
        for user, url in (
            (self.counselor, self.counselor_pdf),
            (self.admin, self.admin_pdf),
            (self.supervisor, self.supervisor_pdf),
        ):
            _assert_blocked(self, _http(user).get(url))

    def test_assigned_supervisor_can_revoke_an_admin_approval(self):
        approval = approve_training_certificate(self.counselor, self.admin)
        before = self._snapshot(approval)
        response = _http(self.supervisor).post(self.supervisor_revoke, follow=True)
        self.assertContains(response, "최근 승인 취소: 김수퍼 / ")
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_UNAPPROVED,
        )
        self.assertEqual(self._snapshot(approval), before)
        revocation = approval.revocation
        self.assertEqual(revocation.revoked_by, self.supervisor)
        self.assertEqual(revocation.revoked_role, UserRole.SUPERVISOR)

    def test_other_cohort_supervisor_cannot_revoke(self):
        approve_training_certificate(self.counselor, self.admin)
        denied = _http(self.outsider).post(self.supervisor_revoke)
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(TrainingCertificateApprovalRevocation.objects.exists())
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_APPROVED,
        )

    def test_counselor_cannot_revoke(self):
        approve_training_certificate(self.counselor, self.admin)
        self.assertEqual(_http(self.counselor).post(self.admin_revoke).status_code, 403)
        self.assertEqual(_http(self.counselor).post(self.supervisor_revoke).status_code, 403)
        self.assertEqual(_http(self.counselor).get(self.admin_revoke).status_code, 403)
        self.assertFalse(TrainingCertificateApprovalRevocation.objects.exists())

    def test_duplicate_revoke_does_not_add_another_row(self):
        approval = approve_training_certificate(self.counselor, self.admin)
        first = _http(self.admin).post(self.admin_revoke)
        self.assertEqual(first.status_code, 302)
        again = _http(self.admin).post(self.admin_revoke)
        self.assertEqual(again.status_code, 400)
        self.assertEqual(
            TrainingCertificateApprovalRevocation.objects.filter(approval=approval).count(),
            1,
        )
        self.assertEqual(TrainingCertificateApproval.objects.count(), 1)

    def test_revoke_targets_the_current_hash_approval_only(self):
        older = approve_training_certificate(self.counselor, self.admin)
        older_fields = self._snapshot(older)
        _journals(self.case, 1)
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_REAPPROVAL_REQUIRED,
        )
        missed = _http(self.admin).post(self.admin_revoke)
        self.assertEqual(missed.status_code, 400)
        self.assertFalse(TrainingCertificateApprovalRevocation.objects.exists())
        current = approve_training_certificate(self.counselor, self.supervisor)
        current_fields = self._snapshot(current)
        self.assertNotEqual(older.content_hash, current.content_hash)
        revoked = _http(self.admin).post(self.admin_revoke)
        self.assertEqual(revoked.status_code, 302)
        self.assertEqual(
            TrainingCertificateApprovalRevocation.objects.get().approval_id,
            current.pk,
        )
        self.assertFalse(
            TrainingCertificateApprovalRevocation.objects.filter(approval=older).exists()
        )
        self.assertEqual(self._snapshot(older), older_fields)
        self.assertEqual(self._snapshot(current), current_fields)
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_UNAPPROVED,
        )

    def test_reapproval_after_revoke_adds_a_row_and_restores_pdf(self):
        original = approve_training_certificate(self.counselor, self.admin)
        original_fields = self._snapshot(original)
        _http(self.admin).post(self.admin_revoke)
        renewed = _http(self.supervisor).post(self.supervisor_approve)
        self.assertEqual(renewed.status_code, 302)
        self.assertEqual(TrainingCertificateApproval.objects.count(), 2)
        self.assertEqual(TrainingCertificateApprovalRevocation.objects.count(), 1)
        self.assertEqual(self._snapshot(original), original_fields)
        self.assertTrue(
            TrainingCertificateApprovalRevocation.objects.filter(approval=original).exists()
        )
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["status"], STATUS_APPROVED)
        self.assertNotEqual(status["latest"].pk, original.pk)
        self.assertFalse(
            TrainingCertificateApprovalRevocation.objects.filter(
                approval=status["latest"]
            ).exists()
        )
        self.assertEqual(status["latest"].content_hash, status["content_hash"])
        self.assertEqual(status["latest"].content_hash, original.content_hash)
        pdf = _http(self.counselor).get(self.counselor_pdf)
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        page = _http(self.admin).get(self.admin_detail, follow=True)
        self.assertContains(page, ">승인 취소</button>")
        self.assertNotContains(page, "최근 승인 취소:")

        _journals(self.case, 1)
        changed = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(changed["status"], STATUS_REAPPROVAL_REQUIRED)
        self.assertEqual(TrainingCertificateApproval.objects.count(), 2)
        self.assertEqual(TrainingCertificateApprovalRevocation.objects.count(), 1)
        _assert_blocked(self, _http(self.admin).get(self.admin_pdf))
        again = _http(self.supervisor).get(self.supervisor_detail, follow=True)
        self.assertContains(again, "재승인 필요")
        self.assertContains(again, "수련확인증 재승인")
        self.assertNotContains(again, "revokeTrainingCertificateModal")

    def test_metrics_change_after_revoke_stays_unapproved_until_a_new_approval(self):
        approve_training_certificate(self.counselor, self.admin)
        revoke_training_certificate(self.counselor, self.supervisor)
        _journals(self.case, 1)
        status = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(status["status"], STATUS_REAPPROVAL_REQUIRED)
        self.assertNotEqual(status["status"], STATUS_APPROVED)
        self.assertFalse(status["can_issue"])
        _assert_blocked(self, _http(self.counselor).get(self.counselor_pdf))
        approve_training_certificate(self.counselor, self.admin)
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_APPROVED,
        )

    def test_dashboard_uses_the_unapproved_notice_after_revoke(self):
        approve_training_certificate(self.counselor, self.admin)
        dashboard = reverse("counselor:dashboard")
        ready = _http(self.counselor).get(dashboard)
        certificate_href = (
            f"{self.counselor_pdf}?enrollment={self.case.training_enrollment_id}"
        )
        self.assertContains(ready, "승인완료")
        self.assertContains(ready, f'href="{certificate_href}"')
        revoke_training_certificate(self.counselor, self.admin)
        blocked = _http(self.counselor).get(dashboard)
        self.assertContains(blocked, "미승인")
        self.assertContains(blocked, MESSAGE_UNAPPROVED)
        self.assertNotContains(blocked, "승인완료")
        self.assertNotContains(blocked, "재승인 필요")
        self.assertNotContains(blocked, certificate_href)
        self.assertContains(blocked, "trainingCertificateNoticeModal")

    def test_get_does_not_revoke(self):
        approve_training_certificate(self.counselor, self.admin)
        self.assertEqual(_http(self.admin).get(self.admin_revoke).status_code, 405)
        self.assertFalse(TrainingCertificateApprovalRevocation.objects.exists())
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_APPROVED,
        )

    def test_revoked_latest_approval_does_not_fall_back_to_older_same_hash_approval(self):
        older = approve_training_certificate(self.counselor, self.supervisor)
        latest = TrainingCertificateApproval.objects.create(
            counselor=self.counselor,
            training_enrollment=older.training_enrollment,
            approved_by=self.admin,
            approved_role=UserRole.ADMIN,
            content_hash=older.content_hash,
        )
        self.assertGreater(latest.pk, older.pk)
        self.assertEqual(older.content_hash, latest.content_hash)
        before = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(before["status"], STATUS_APPROVED)
        self.assertEqual(before["approved_by_name"], "홍길동")
        self.assertTrue(before["can_issue"])

        page = _http(self.admin).post(self.admin_revoke, follow=True)
        self.assertContains(page, _status_line("미승인", 1, 1))
        self.assertContains(page, "최근 승인 취소: 홍길동 / ")
        self.assertContains(page, "수련확인증 승인")
        self.assertNotContains(page, "revokeTrainingCertificateModal")
        self.assertNotContains(page, "승인완료")
        self.assertEqual(
            TrainingCertificateApprovalRevocation.objects.get().approval_id,
            latest.pk,
        )
        self.assertFalse(
            TrainingCertificateApprovalRevocation.objects.filter(approval=older).exists()
        )
        revoked = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(revoked["status"], STATUS_UNAPPROVED)
        self.assertFalse(revoked["can_issue"])
        self.assertEqual(revoked["revoked_by_name"], "홍길동")
        for user, url in (
            (self.counselor, self.counselor_pdf),
            (self.admin, self.admin_pdf),
            (self.supervisor, self.supervisor_pdf),
        ):
            _assert_blocked(self, _http(user).get(url))

        renewed = approve_training_certificate(self.counselor, self.supervisor)
        self.assertNotIn(renewed.pk, {older.pk, latest.pk})
        self.assertEqual(
            TrainingCertificateApproval.objects.filter(counselor=self.counselor).count(),
            3,
        )
        self.assertEqual(TrainingCertificateApprovalRevocation.objects.count(), 1)
        self.assertEqual(renewed.content_hash, older.content_hash)
        restored = get_training_certificate_approval_status(self.counselor)
        self.assertEqual(restored["status"], STATUS_APPROVED)
        self.assertEqual(restored["latest"].pk, renewed.pk)
        self.assertEqual(restored["approved_by_name"], "김수퍼")
        pdf = _http(self.counselor).get(self.counselor_pdf)
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_already_approved_does_not_create_another_same_hash_row(self):
        first = approve_training_certificate(self.counselor, self.admin)
        second = approve_training_certificate(self.counselor, self.supervisor)
        self.assertEqual(second.pk, first.pk)
        posted = _http(self.supervisor).post(self.supervisor_approve)
        self.assertEqual(posted.status_code, 302)
        self.assertEqual(
            TrainingCertificateApproval.objects.filter(counselor=self.counselor).count(),
            1,
        )
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_APPROVED,
        )

        revoke_training_certificate(self.counselor, self.admin)
        third = approve_training_certificate(self.counselor, self.supervisor)
        self.assertNotEqual(third.pk, first.pk)
        self.assertEqual(
            TrainingCertificateApproval.objects.filter(counselor=self.counselor).count(),
            2,
        )
        self.assertEqual(
            get_training_certificate_approval_status(self.counselor)["status"],
            STATUS_APPROVED,
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


class SupervisorDetailTargetCounselorTests(TestCase):
    def test_url_counselor_counts_are_not_replaced_by_cohort_peers(self):
        cohort = 51
        first = _counselor("가가가", cohort)
        target = _counselor("나나나", cohort)
        last = _counselor("다다다", cohort)
        for owner, prefix, total in ((first, "FIRST", 4), (last, "LAST", 4)):
            for index in range(total):
                _case(owner, _client(f"{prefix}{index}"), f"{prefix}-{index}")
        pending = _case(target, _client("대상진행"), "TARGET-OPEN")
        submitted = _case(target, _client("대상제출"), "TARGET-DONE")
        submitted.status = CaseStatus.CLOSED
        submitted.save(update_fields=["status"])
        _submit(submitted)

        supervisor = _supervisor("대상수퍼@cert.example.com", "대상수퍼", [cohort])
        admin = _user("대상관리@cert.example.com", "대상관리", UserRole.ADMIN)
        supervisor_detail = reverse(
            "supervisor:counselor_training_progress_detail",
            kwargs={"counselor_id": target.pk},
        )
        admin_detail = reverse(
            "admin_panel:counselor_training_progress_detail",
            kwargs={"counselor_id": target.pk},
        )
        expected = _status_line("미승인", 2, 1)
        peer_line = _status_line("미승인", 4, 0)

        supervisor_page = _http(supervisor).get(supervisor_detail, follow=True)
        admin_page = _http(admin).get(admin_detail, follow=True)
        self.assertEqual(supervisor_page.context["counselor"].pk, target.pk)
        self.assertEqual(admin_page.context["counselor"].pk, target.pk)
        self.assertEqual(
            supervisor_page.context["certificate_approval"]["assigned_case_count"],
            2,
        )
        self.assertEqual(
            supervisor_page.context["certificate_approval"]["submitted_case_count"],
            1,
        )
        self.assertEqual(
            admin_page.context["certificate_approval"]["assigned_case_count"],
            2,
        )
        self.assertEqual(
            admin_page.context["certificate_approval"]["submitted_case_count"],
            1,
        )
        self.assertEqual(
            admin_page.context["certificate_approval"]["status"],
            supervisor_page.context["certificate_approval"]["status"],
        )
        self.assertContains(supervisor_page, expected)
        self.assertContains(admin_page, expected)
        self.assertContains(supervisor_page, "나나나 상담사 수련현황")
        self.assertNotContains(supervisor_page, peer_line)
        self.assertNotContains(admin_page, peer_line)
        peer_page = _http(supervisor).get(
            reverse(
                "supervisor:counselor_training_progress_detail",
                kwargs={"counselor_id": last.pk},
            ),
            follow=True,
        )
        self.assertContains(peer_page, peer_line)
        self.assertEqual(peer_page.context["counselor"].pk, last.pk)
        self.assertIsNone(pending.records_submitted_at)
        self.assertIsNotNone(submitted.records_submitted_at)

        TrainingCertificateApproval.objects.create(
            counselor=last,
            approved_by=admin,
            approved_role=UserRole.ADMIN,
            content_hash="a" * 64,
        )
        again = _http(supervisor).get(supervisor_detail, follow=True)
        self.assertContains(again, expected)
        self.assertEqual(again.context["counselor"].pk, target.pk)
        self.assertEqual(
            again.context["certificate_approval"]["status"],
            STATUS_UNAPPROVED,
        )

        pdf_url = reverse(
            "supervisor:counselor_training_certificate",
            kwargs={"counselor_id": target.pk},
        )
        self.assertIn(str(target.pk), pdf_url)
        self.assertNotIn(str(last.pk), pdf_url)
        self.assertEqual(_http(supervisor).get(pdf_url).status_code, 403)

        listing = _http(supervisor).get(
            reverse("supervisor:training_progress"),
            {"cohort": str(cohort)},
        )
        self.assertContains(
            listing,
            reverse(
                "supervisor:enrollment_training_progress_detail",
                kwargs={
                    "enrollment_id": TrainingEnrollment.objects.get(counselor=target).pk
                },
            ),
        )
        self.assertContains(listing, "나나나")
