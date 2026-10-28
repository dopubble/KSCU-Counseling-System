"""전문상담사 수련확인서 PDF."""

import uuid
from datetime import date
from io import BytesIO
from unittest.mock import patch

import pikepdf
from django.test import Client as HttpClient, TestCase
from django.urls import resolve, reverse

from apps.accounts.models import CounselorProfile, SupervisorProfile, User, UserRole, UserStatus
from apps.counseling.training_certificate_pdf import (
    build_training_certificate_context,
    build_training_certificate_pdf,
    training_certificate_response,
)
from apps.counseling.training_progress import get_counselor_training_progress


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
        context = build_training_certificate_context(counselor, progress, date(2026, 10, 28))

        self.assertIs(context["progress"], progress)
        self.assertEqual(context["title"], "전문상담사 수련확인서")
        self.assertEqual(
            context["identity"],
            [
                ("성명", "확인서상담"),
                ("기수", "7기"),
                ("생년월일", "1991년 4월 8일"),
            ],
        )
        self.assertEqual(
            context["rows"],
            [
                [
                    "접수면접",
                    f"{progress['intake_required_count']}건",
                    f"{progress['intake_completed_count']}건",
                    progress["intake_completed_label"],
                ],
                [
                    "상담자 경험",
                    f"{progress['counseling_assigned_count']}회기",
                    f"{progress['journal_completed_count']}회기",
                    progress["counseling_completed_label"],
                ],
                [
                    "종결기록지",
                    f"{progress['termination_required_count']}건",
                    f"{progress['termination_completed_count']}건",
                    "—",
                ],
                [
                    "수퍼비전",
                    f"{progress['supervision_required_count']}회",
                    (
                        f"{progress['supervision_attended_count']}회 출석 "
                        f"({progress['supervision_attendance_percent']}%)"
                    ),
                    "—",
                ],
                [
                    "사례보고서",
                    f"{progress['case_report_required_count']}건",
                    f"{progress['case_report_submitted_count']}건",
                    "—",
                ],
                [
                    "공개사례발표회",
                    "—",
                    f"{progress['public_presentation_participation_count']}회 참여",
                    "—",
                ],
            ],
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
        )
        labels = [label for label, _value in context["identity"]]
        self.assertEqual(context["identity"][1], ("기수", "—"))
        self.assertNotIn("생년월일", labels)

    def test_pdf_is_a_single_a4_page(self):
        counselor = _counselor("한페이지", cohort=1, birth_date=date(1988, 12, 3))
        context = build_training_certificate_context(
            counselor,
            get_counselor_training_progress(counselor),
            date(2026, 10, 28),
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
        with self.assertNumQueries(4):
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
        self.assertContains(dashboard, "수련확인서 출력")
        self.assertContains(dashboard, self.own_url)

        admin_http = HttpClient()
        admin_http.force_login(self.admin)
        listing = admin_http.get(reverse("admin_panel:counselor_list"))
        self.assertContains(listing, "수련확인서")
        self.assertContains(listing, self.admin_url)
