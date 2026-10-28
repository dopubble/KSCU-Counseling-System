"""전문상담사 수련확인서 PDF."""

import uuid
from datetime import date
from io import BytesIO
from unittest.mock import patch

import pikepdf
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as HttpClient, TestCase
from django.utils import timezone
from django.urls import resolve, reverse

from apps.accounts.models import CounselorProfile, SupervisorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CasePresentationPost,
    CounselingApplication,
    CounselingMethod,
    SupervisionAttendance,
    SupervisionAttendanceStatus,
    SupervisionSession,
    SupervisionSessionStatus,
)
from apps.counseling.training_certificate import (
    intake_delivery_mode_label,
    journal_delivery_mode_label,
    supervisor_label_for_counselor,
    supervision_attended_time_label,
)
from apps.counseling.training_certificate_pdf import (
    build_training_certificate_context,
    build_training_certificate_pdf,
    training_certificate_response,
)
from apps.counseling.training_progress import get_counselor_training_progress
from apps.documents.models import ConsentDocType, ConsentDocument
from apps.sessions_app.models import CounselingJournal


def _user(email, name, role):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


def _counselor(name, *, cohort=3, birth_date=None):
    user = _user(f"{name}@example.com", name, UserRole.COUNSELOR)
    profile = user.counselor_profile
    profile.cohort = cohort
    profile.birth_date = birth_date
    profile.is_approved = cohort is not None
    profile.save(update_fields=["cohort", "birth_date", "is_approved", "updated_at"])
    return user


class TrainingCertificateContextTests(TestCase):
    def test_context_uses_training_progress_result(self):
        counselor = _counselor("확인서상담", cohort=7, birth_date=date(1991, 4, 8))
        progress = get_counselor_training_progress(counselor)
        context = build_training_certificate_context(
            counselor,
            progress,
            date(2026, 10, 28),
            supervisor_label="홍길동",
            intake_mode="비대면",
            journal_mode="대면·비대면 혼합",
            supervision_time="6시간 40분",
        )

        self.assertIs(context["progress"], progress)
        self.assertEqual(context["title"], "수련확인증")
        self.assertEqual(
            context["identity"],
            [
                ("성명", "확인서상담"),
                ("생년월일", "1991년 4월 8일"),
                ("수퍼바이저", "홍길동"),
            ],
        )
        self.assertNotIn("기수", [label for label, _value in context["identity"]])
        self.assertEqual(context["columns"], ["수련항목", "수련시간", "진행방식"])
        self.assertNotIn("인정시간", context["columns"])
        self.assertEqual(
            context["rows"],
            [
                ["접수면접", progress["intake_completed_label"], "비대면"],
                ["상담자 경험", progress["counseling_completed_label"], "대면·비대면 혼합"],
                ["수퍼비전", "6시간 40분", "비대면"],
                ["사례보고서", f"{progress['case_report_submitted_count']}사례", "비대면"],
            ],
        )
        row_names = [row[0] for row in context["rows"]]
        self.assertNotIn("종결기록지", row_names)
        self.assertNotIn("공개사례발표회", row_names)
        self.assertEqual(
            context["method_notice"],
            "※ 접수면접 및 상담자 경험의 진행방식은 현재 사례에 등록된 상담방식을 기준으로 표시합니다.",
        )
        self.assertEqual(
            context["confirmation"],
            "위와 같이 전문상담사 수련과정의 수련 내역을 확인합니다.",
        )
        self.assertEqual(context["issued_on_label"], "2026년 10월 28일")
        self.assertEqual(context["issuer"], "숭실사이버대학교 평생교육원")
        flat = " ".join(" ".join(row) for row in context["rows"])
        self.assertNotIn("총 수련시간", flat)
        self.assertNotIn("결석", flat)

    def test_missing_birth_date_and_cohort_are_omitted_or_dashed(self):
        counselor = _counselor("기간없음", cohort=None, birth_date=None)
        context = build_training_certificate_context(
            counselor,
            get_counselor_training_progress(counselor),
            date(2026, 1, 5),
            supervisor_label="—",
            intake_mode="—",
            journal_mode="—",
            supervision_time="0분",
        )
        labels = [label for label, _value in context["identity"]]
        self.assertEqual(context["identity"], [("성명", "기간없음"), ("수퍼바이저", "—")])
        self.assertNotIn("생년월일", labels)
        self.assertNotIn("기수", labels)

    def test_pdf_is_a_single_a4_page(self):
        counselor = _counselor("한페이지", cohort=1, birth_date=date(1988, 12, 3))
        context = build_training_certificate_context(
            counselor,
            get_counselor_training_progress(counselor),
            date(2026, 10, 28),
            supervisor_label="—",
            intake_mode="—",
            journal_mode="—",
            supervision_time="0분",
        )
        pdf_bytes = build_training_certificate_pdf(context)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        with pikepdf.open(BytesIO(pdf_bytes)) as pdf:
            self.assertEqual(len(pdf.pages), 1)
            box = pdf.pages[0].MediaBox
            self.assertAlmostEqual(float(box[2]), 595.27, places=1)
            self.assertAlmostEqual(float(box[3]), 841.89, places=1)


class TrainingCertificateAccessTests(TestCase):
    def setUp(self):
        self.counselor = _counselor("본인상담", cohort=2, birth_date=date(1994, 2, 1))
        self.other = _counselor("다른상담", cohort=2)
        self.admin = _user("확인서관리@example.com", "확인서관리", UserRole.ADMIN)
        self.supervisor = _user("확인서수퍼@example.com", "확인서수퍼", UserRole.SUPERVISOR)
        SupervisorProfile.objects.filter(user=self.supervisor).update(assigned_cohorts=[2])
        self.client = _user("확인서내담@example.com", "확인서내담", UserRole.CLIENT)
        self.own_url = reverse("counselor:training_certificate")
        self.admin_url = reverse(
            "admin_panel:counselor_training_certificate",
            args=[self.counselor.pk],
        )

    def test_counselor_endpoint_has_no_counselor_id(self):
        match = resolve(self.own_url)
        self.assertEqual(match.kwargs, {})

    def test_counselor_receives_own_pdf(self):
        http = HttpClient()
        http.force_login(self.counselor)
        response = http.get(self.own_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_counselor_pdf_uses_the_same_progress_object(self):
        http = HttpClient()
        http.force_login(self.counselor)
        sentinel = get_counselor_training_progress(self.counselor)
        with (
            patch(
                "apps.counseling.training_progress.get_counselor_training_progress",
                return_value=sentinel,
            ) as progress,
            patch(
                "apps.counseling.training_certificate_pdf.build_training_certificate_pdf",
                return_value=b"%PDF-1.4",
            ) as builder,
            patch(
                "apps.counseling.training_certificate_pdf.timezone.localdate",
                return_value=date(2026, 10, 28),
            ),
        ):
            response = http.get(self.own_url)
        self.assertEqual(response.status_code, 200)
        progress.assert_called_once()
        self.assertEqual(progress.call_args.args[0].pk, self.counselor.pk)
        context = builder.call_args.args[0]
        self.assertIs(context["progress"], sentinel)
        self.assertEqual(context["identity"][0], ("성명", "본인상담"))
        self.assertEqual(context["issued_on_label"], "2026년 10월 28일")
        self.assertNotEqual(context["identity"][0][1], self.other.name)

    def test_one_certificate_does_not_query_per_case(self):
        counselor = User.objects.select_related("counselor_profile").get(pk=self.counselor.pk)
        with self.assertNumQueries(8):
            response = training_certificate_response(counselor)
        self.assertEqual(response.status_code, 200)

    def test_admin_can_download_a_counselor_pdf(self):
        http = HttpClient()
        http.force_login(self.admin)
        response = http.get(self.admin_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_non_admin_cannot_open_admin_endpoint(self):
        http = HttpClient()
        http.force_login(self.supervisor)
        self.assertEqual(http.get(self.admin_url).status_code, 403)
        http.force_login(self.counselor)
        self.assertEqual(http.get(self.admin_url).status_code, 403)

    def test_supervisor_cannot_open_counselor_endpoint(self):
        http = HttpClient()
        http.force_login(self.supervisor)
        self.assertEqual(http.get(self.own_url).status_code, 403)

    def test_unknown_counselor_is_404(self):
        http = HttpClient()
        http.force_login(self.admin)
        url = reverse("admin_panel:counselor_training_certificate", args=[uuid.uuid4()])
        self.assertEqual(http.get(url).status_code, 404)

    def test_non_counselor_target_is_404(self):
        http = HttpClient()
        http.force_login(self.admin)
        url = reverse("admin_panel:counselor_training_certificate", args=[self.client.pk])
        self.assertEqual(http.get(url).status_code, 404)

    def test_counselor_without_profile_is_404(self):
        CounselorProfile.objects.filter(user=self.counselor).delete()
        http = HttpClient()
        http.force_login(self.admin)
        self.assertEqual(http.get(self.admin_url).status_code, 404)

    def test_dashboard_and_admin_list_link_to_the_certificate(self):
        counselor_http = HttpClient()
        counselor_http.force_login(self.counselor)
        dashboard = counselor_http.get(reverse("counselor:dashboard"))
        self.assertContains(dashboard, "수련확인증 출력")
        self.assertContains(dashboard, self.own_url)

        admin_http = HttpClient()
        admin_http.force_login(self.admin)
        listing = admin_http.get(reverse("admin_panel:counselor_list"))
        self.assertContains(listing, "수련확인증")
        self.assertContains(listing, self.admin_url)


def _case(counselor, client, number, method):
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
        total_sessions=10,
        remaining_sessions=10,
    )


def _intake(case):
    return ConsentDocument.objects.create(
        client=case.client,
        application=case.application,
        doc_type=ConsentDocType.INTAKE,
        file="consents/intake.pdf",
        uploaded_by=case.counselor,
    )


def _journal(case, session_number, *, appointment=None, is_draft=False):
    return CounselingJournal.objects.create(
        case=case,
        counselor=case.counselor,
        session_number=session_number,
        appointment=appointment,
        is_draft=is_draft,
        subjective="S",
        objective="O",
        assessment="A",
    )


class TrainingCertificateDetailTests(TestCase):
    def test_supervisor_names_follow_assigned_cohort(self):
        counselor = _counselor("기수상담", cohort=4)
        other = _counselor("다른기수", cohort=9)
        kim = _user("김수퍼@example.com", "김수퍼", UserRole.SUPERVISOR)
        park = _user("박수퍼@example.com", "박수퍼", UserRole.SUPERVISOR)
        duplicate = _user("김수퍼2@example.com", "김수퍼", UserRole.SUPERVISOR)
        elsewhere = _user("외수퍼@example.com", "외수퍼", UserRole.SUPERVISOR)
        SupervisorProfile.objects.filter(user=kim).update(assigned_cohorts=[4])
        SupervisorProfile.objects.filter(user=park).update(assigned_cohorts=["4", 8])
        SupervisorProfile.objects.filter(user=duplicate).update(assigned_cohorts=[4])
        SupervisorProfile.objects.filter(user=elsewhere).update(assigned_cohorts=[9])

        self.assertEqual(
            supervisor_label_for_counselor(counselor),
            "김수퍼 교수, 박수퍼 교수",
        )
        self.assertEqual(supervisor_label_for_counselor(other), "외수퍼 교수")

        alone = _counselor("담당없음", cohort=3)
        self.assertEqual(supervisor_label_for_counselor(alone), "—")

    def test_intake_mode_uses_current_case_kinds_without_counts(self):
        counselor = _counselor("면접상담", cohort=1)
        client_a = _user("면접내담1@example.com", "면접내담1", UserRole.CLIENT)
        client_b = _user("면접내담2@example.com", "면접내담2", UserRole.CLIENT)
        client_c = _user("면접내담3@example.com", "면접내담3", UserRole.CLIENT)
        in_person = _case(counselor, client_a, "INT-1", CounselingMethod.IN_PERSON)
        remote = _case(counselor, client_b, "INT-2", CounselingMethod.REMOTE)
        unknown = _case(counselor, client_c, "INT-3", CounselingMethod.IN_PERSON)
        _intake(in_person)
        self.assertEqual(intake_delivery_mode_label(counselor), "대면")

        Case.objects.filter(pk=in_person.pk).update(counseling_method=CounselingMethod.REMOTE)
        self.assertEqual(intake_delivery_mode_label(counselor), "비대면")
        Case.objects.filter(pk=in_person.pk).update(counseling_method=CounselingMethod.IN_PERSON)

        _intake(remote)
        self.assertEqual(intake_delivery_mode_label(counselor), "대면·비대면 혼합")

        Case.objects.filter(pk=unknown.pk).update(counseling_method="")
        _intake(unknown)
        self.assertEqual(
            intake_delivery_mode_label(counselor),
            "대면·비대면 혼합 · 일부 확인불가",
        )
        ConsentDocument.objects.filter(application=remote.application).delete()
        self.assertEqual(intake_delivery_mode_label(counselor), "대면 · 일부 확인불가")

    def test_journal_mode_uses_distinct_cases_not_journal_counts(self):
        counselor = _counselor("일지상담", cohort=1)
        client = _user("일지내담@example.com", "일지내담", UserRole.CLIENT)
        other_client = _user("다른내담@example.com", "다른내담", UserRole.CLIENT)
        in_person = _case(counselor, client, "JRN-1", CounselingMethod.IN_PERSON)
        for session_number in range(1, 11):
            _journal(in_person, session_number)
        _journal(in_person, 11, is_draft=True)
        label = journal_delivery_mode_label(counselor)
        self.assertEqual(label, "대면")
        self.assertNotIn("10", label)
        self.assertNotIn("회", label)

        remote = _case(counselor, other_client, "JRN-2", CounselingMethod.REMOTE)
        _journal(remote, 1)
        self.assertEqual(journal_delivery_mode_label(counselor), "대면·비대면 혼합")

        unknown = _case(counselor, client, "JRN-3", CounselingMethod.IN_PERSON)
        Case.objects.filter(pk=unknown.pk).update(counseling_method="LEGACY")
        _journal(unknown, 1)
        self.assertEqual(
            journal_delivery_mode_label(counselor),
            "대면·비대면 혼합 · 일부 확인불가",
        )

    def test_missing_logo_still_builds_pdf(self):
        counselor = _counselor("로고없음", cohort=1)
        context = build_training_certificate_context(
            counselor,
            get_counselor_training_progress(counselor),
            date(2026, 10, 28),
            supervisor_label="—",
            intake_mode="—",
            journal_mode="대면",
            supervision_time="0분",
        )
        with patch(
            "apps.counseling.training_certificate_pdf._logo_flowable",
            return_value=None,
        ):
            pdf_bytes = build_training_certificate_pdf(context)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        with pikepdf.open(BytesIO(pdf_bytes)) as pdf:
            self.assertEqual(len(pdf.pages), 1)


def _certificate_context(counselor, progress):
    return build_training_certificate_context(
        counselor,
        progress,
        date(2026, 10, 28),
        supervisor_label="—",
        intake_mode="비대면",
        journal_mode="비대면",
        supervision_time=supervision_attended_time_label(counselor),
    )


def _supervision_session(cohort, number, status, minutes):
    return SupervisionSession.objects.create(
        cohort=cohort,
        session_number=number,
        scheduled_at=timezone.now(),
        status=status,
        duration_minutes=minutes,
    )


def _attend(session, counselor, status):
    return SupervisionAttendance.objects.create(
        session=session,
        counselor=counselor.counselor_profile,
        status=status,
    )


class TrainingCertificateRecordTests(TestCase):
    def test_credit_hours_and_case_report_counts(self):
        counselor = _counselor("시간상담", cohort=5)
        client = _user("시간내담@example.com", "시간내담", UserRole.CLIENT)
        case = _case(counselor, client, "TIME-1", CounselingMethod.REMOTE)
        _intake(case)
        for session_number in range(1, 6):
            _journal(case, session_number)
        progress = get_counselor_training_progress(counselor)
        context = _certificate_context(counselor, progress)
        self.assertEqual(context["rows"][0][1], "1시간")
        self.assertEqual(context["rows"][1][1], "5시간")
        self.assertEqual(context["rows"][3], ["사례보고서", "0사례", "비대면"])

        CasePresentationPost.objects.create(
            cohort=5,
            author=counselor,
            case=case,
            title="보고서1",
            file=SimpleUploadedFile("보고서1.pdf", b"%PDF-1.4"),
        )
        CasePresentationPost.objects.create(
            cohort=5,
            author=counselor,
            case=case,
            title="보고서2",
            file=SimpleUploadedFile("보고서2.pdf", b"%PDF-1.4"),
        )
        progress = get_counselor_training_progress(counselor)
        self.assertEqual(progress["case_report_submitted_count"], 1)
        context = _certificate_context(counselor, progress)
        self.assertEqual(context["rows"][3][1], "1사례")

        other_client = _user("시간내담2@example.com", "시간내담2", UserRole.CLIENT)
        other_case = _case(counselor, other_client, "TIME-2", CounselingMethod.REMOTE)
        CasePresentationPost.objects.create(
            cohort=5,
            author=counselor,
            case=other_case,
            title="보고서3",
            file=SimpleUploadedFile("보고서3.pdf", b"%PDF-1.4"),
        )
        progress = get_counselor_training_progress(counselor)
        self.assertEqual(progress["case_report_submitted_count"], 2)
        context = _certificate_context(counselor, progress)
        self.assertEqual(context["rows"][3][1], "2사례")

    def test_supervision_time_uses_present_completed_sessions_only(self):
        counselor = _counselor("출석시간", cohort=6)
        other = _counselor("다른기수출석", cohort=7)
        present = _supervision_session(6, 1, SupervisionSessionStatus.COMPLETED, 480)
        absent = _supervision_session(6, 2, SupervisionSessionStatus.COMPLETED, 60)
        unchecked = _supervision_session(6, 3, SupervisionSessionStatus.COMPLETED, 60)
        scheduled = _supervision_session(6, 4, SupervisionSessionStatus.SCHEDULED, 60)
        cancelled = _supervision_session(6, 5, SupervisionSessionStatus.CANCELLED, 60)
        other_present = _supervision_session(7, 1, SupervisionSessionStatus.COMPLETED, 120)
        _attend(present, counselor, SupervisionAttendanceStatus.PRESENT)
        _attend(absent, counselor, SupervisionAttendanceStatus.ABSENT)
        _attend(unchecked, counselor, SupervisionAttendanceStatus.UNCHECKED)
        _attend(scheduled, counselor, SupervisionAttendanceStatus.PRESENT)
        _attend(cancelled, counselor, SupervisionAttendanceStatus.PRESENT)
        _attend(other_present, other, SupervisionAttendanceStatus.PRESENT)

        self.assertEqual(supervision_attended_time_label(counselor), "8시간")
        present.duration_minutes = 450
        present.save(update_fields=["duration_minutes"])
        self.assertEqual(supervision_attended_time_label(counselor), "7시간 30분")
