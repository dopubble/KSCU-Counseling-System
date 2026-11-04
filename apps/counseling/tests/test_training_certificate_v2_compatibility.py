"""기존 certificate-metrics-v2 승인이 2기 실적과 프로필 기수 변경 뒤에도 유지되는지 검증한다."""

from datetime import date, datetime

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
    TrainingCertificateApprovalRevocation,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.counseling.training_certificate import (
    STATUS_APPROVED,
    STATUS_REAPPROVAL_REQUIRED,
    STATUS_UNAPPROVED,
    approve_training_certificate,
    build_counselor_training_certificate_context,
    build_training_certificate_approval_payload,
    calculate_training_certificate_hash,
    get_training_certificate_approval_status,
    revoke_training_certificate,
)
from apps.documents.models import ConsentDocType, ConsentDocument
from apps.sessions_app.models import CounselingJournal

_V2_FIELDS = (
    "supervisor",
    "issued_on",
    "cases_5plus",
    "case_report_count",
    "own_case_supervision_posts",
    "intake_minutes",
    "counseling_minutes",
    "supervision_minutes",
)


def _user(email, name, role):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


def _counselor(name, cohort):
    user = _user(f"{name}@v2.example.com", name, UserRole.COUNSELOR)
    profile = user.counselor_profile
    profile.cohort = cohort
    profile.birth_date = date(1992, 5, 6)
    profile.is_approved = True
    profile.save(update_fields=["cohort", "birth_date", "is_approved", "updated_at"])
    return user


def _supervisor(name, cohorts):
    user = _user(f"{name}@v2.example.com", name, UserRole.SUPERVISOR)
    profile = user.supervisor_profile
    profile.assigned_cohorts = cohorts
    profile.save(update_fields=["assigned_cohorts", "updated_at"])
    return user


def _when(year, month, day):
    return timezone.make_aware(datetime(year, month, day, 10, 0))


def _case(counselor, enrollment, number, journals):
    client = _user(f"client-{number}@v2.example.com", f"내담자{number}", UserRole.CLIENT)
    application = CounselingApplication.objects.create(
        client=client,
        counseling_types=["진로상담"],
        reason="test",
        counseling_method=CounselingMethod.IN_PERSON,
        status=ApplicationStatus.IN_PROGRESS,
    )
    case = Case.objects.create(
        application=application,
        client=client,
        counselor=counselor,
        case_number=number,
        counseling_method=CounselingMethod.IN_PERSON,
        total_sessions=10,
        remaining_sessions=10,
        status=CaseStatus.ACTIVE,
        training_enrollment=enrollment,
    )
    case.records_submitted_at = timezone.now()
    case.records_submitted_by = counselor
    case.save(update_fields=["records_submitted_at", "records_submitted_by"])
    ConsentDocument.objects.create(
        client=client,
        application=application,
        doc_type=ConsentDocType.INTAKE,
        file="consents/intake.pdf",
        uploaded_by=counselor,
    )
    for session_number in range(1, journals + 1):
        CounselingJournal.objects.create(
            case=case,
            counselor=counselor,
            session_number=session_number,
            is_draft=False,
            counseling_method=CounselingMethod.IN_PERSON,
        )
    return case


def _supervision(counselor, cohort, minutes, when):
    session = SupervisionSession.objects.create(
        cohort=cohort,
        session_number=cohort * 10 + minutes,
        scheduled_at=when,
        status=SupervisionSessionStatus.COMPLETED,
        duration_minutes=minutes,
        counseling_method=CounselingMethod.IN_PERSON,
    )
    SupervisionAttendance.objects.create(
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


def _v2_payload(counselor, context):
    payload = build_training_certificate_approval_payload(counselor, context)
    payload["schema"] = "certificate-metrics-v2"
    payload.pop("training_enrollment_id", None)
    return payload


class HistoricalV2ApprovalTests(TestCase):
    def test_cohort1_v2_approval_survives_cohort2_and_profile_change(self):
        counselor = _counselor("상담A", 1)
        supervisor_one = _supervisor("김1기", [1])
        _supervisor("이2기", [2])
        admin = _user("admin@v2.example.com", "관리자", UserRole.ADMIN)
        first = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        case = _case(counselor, first, "V2-1", journals=5)
        _supervision(counselor, 1, 90, _when(2026, 3, 2))
        _report(counselor, "1기발표", case=case, cohort=1)

        historical = build_counselor_training_certificate_context(counselor)
        historical_payload = build_training_certificate_approval_payload(counselor, historical)
        self.assertEqual(historical_payload["schema"], "certificate-metrics-v2")
        self.assertNotIn("training_enrollment_id", historical_payload)
        historical_hash = calculate_training_certificate_hash(counselor, historical)
        approval = TrainingCertificateApproval.objects.create(
            counselor=counselor,
            training_enrollment=first,
            approved_by=admin,
            approved_role=UserRole.ADMIN,
            content_hash=historical_hash,
        )
        stored_at = approval.approved_at
        stored_by = approval.approved_by_id

        first.status = TrainingEnrollmentStatus.COMPLETED
        first.save(update_fields=["status", "updated_at"])
        second = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=2,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        profile = counselor.counselor_profile
        profile.cohort = 2
        profile.save(update_fields=["cohort", "updated_at"])
        later_case = _case(counselor, second, "V2-2", journals=5)
        _supervision(counselor, 2, 30, _when(2026, 9, 8))
        _report(counselor, "2기발표", case=later_case, cohort=2)

        scoped = build_counselor_training_certificate_context(counselor, first)
        compatible = _v2_payload(counselor, scoped)
        self.assertEqual(
            {field: compatible[field] for field in _V2_FIELDS},
            {field: historical_payload[field] for field in _V2_FIELDS},
        )
        self.assertEqual(compatible, historical_payload)
        self.assertEqual(compatible["supervisor"], "김1기 교수")
        self.assertEqual(compatible["issued_on"], "2026-03-02")
        self.assertEqual(compatible["cases_5plus"], 1)
        self.assertEqual(compatible["case_report_count"], 1)
        self.assertEqual(compatible["own_case_supervision_posts"], 1)
        self.assertEqual(compatible["counseling_minutes"]["in_person"], 300)
        self.assertEqual(compatible["supervision_minutes"]["in_person"], 90)

        status = get_training_certificate_approval_status(
            counselor, training_enrollment=first
        )
        second_status = get_training_certificate_approval_status(
            counselor, training_enrollment=second
        )
        self.assertEqual(status["status"], STATUS_APPROVED)
        self.assertEqual(second_status["status"], STATUS_UNAPPROVED)
        self.assertIn(("수련 기수", "1기"), status["context"]["identity"])
        approval.refresh_from_db()
        self.assertEqual(approval.content_hash, historical_hash)
        self.assertEqual(approval.approved_at, stored_at)
        self.assertEqual(approval.approved_by_id, stored_by)
        self.assertEqual(approval.training_enrollment_id, first.pk)
        self.assertEqual(TrainingCertificateApprovalRevocation.objects.count(), 0)

        client = HttpClient()
        client.force_login(admin)
        issued = client.get(
            reverse(
                "admin_panel:enrollment_training_certificate",
                kwargs={"enrollment_id": first.pk},
            )
        )
        self.assertEqual(issued.status_code, 200)
        self.assertTrue(issued.content.startswith(b"%PDF"))
        blocked_second = client.get(
            reverse(
                "admin_panel:enrollment_training_certificate",
                kwargs={"enrollment_id": second.pk},
            )
        )
        self.assertEqual(blocked_second.status_code, 403)
        for url in (
            reverse("counselor:training_certificate"),
            reverse(
                "admin_panel:counselor_training_certificate",
                kwargs={"counselor_id": counselor.pk},
            ),
            reverse(
                "supervisor:counselor_training_certificate",
                kwargs={"counselor_id": counselor.pk},
            ),
        ):
            actor = counselor if "counselor/training-certificate" in url else (
                supervisor_one if "supervisor" in url else admin
            )
            client.force_login(actor)
            ambiguous = client.get(url)
            self.assertEqual(ambiguous.status_code, 404, url)

        client.force_login(counselor)
        selected = client.get(
            reverse("counselor:training_certificate"),
            {"enrollment": str(first.pk)},
        )
        self.assertEqual(selected.status_code, 200)
        self.assertTrue(selected.content.startswith(b"%PDF"))
        dashboard = client.get(
            reverse("counselor:dashboard"),
            {"enrollment": str(first.pk)},
        )
        self.assertContains(dashboard, "승인완료")
        self.assertContains(dashboard, f"enrollment={first.pk}")
        other = client.get(
            reverse("counselor:dashboard"),
            {"enrollment": str(second.pk)},
        )
        self.assertContains(other, "미승인")
        self.assertNotContains(other, f"enrollment={first.pk}")

    def test_changed_cohort1_metrics_require_reapproval_of_that_enrollment(self):
        counselor = _counselor("변경A", 1)
        supervisor = _supervisor("김변경", [1])
        outsider = _supervisor("이다른", [2])
        admin = _user("변경관리@v2.example.com", "변경관리", UserRole.ADMIN)
        first = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.COMPLETED,
        )
        second = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=2,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        case = _case(counselor, first, "CHG-1", journals=5)
        historical = build_counselor_training_certificate_context(counselor, first)
        historical_payload = _v2_payload(counselor, historical)
        legacy_context = {
            key: value
            for key, value in historical.items()
            if key != "training_enrollment"
        }
        approval = TrainingCertificateApproval.objects.create(
            counselor=counselor,
            training_enrollment=first,
            approved_by=admin,
            approved_role=UserRole.ADMIN,
            content_hash=calculate_training_certificate_hash(counselor, legacy_context),
        )
        stored_hash = approval.content_hash
        self.assertEqual(
            build_training_certificate_approval_payload(counselor, legacy_context)["schema"],
            "certificate-metrics-v2",
        )
        CounselingJournal.objects.create(
            case=case,
            counselor=counselor,
            session_number=6,
            is_draft=False,
            counseling_method=CounselingMethod.IN_PERSON,
        )
        status = get_training_certificate_approval_status(
            counselor, training_enrollment=first
        )
        self.assertEqual(status["status"], STATUS_REAPPROVAL_REQUIRED)
        approval.refresh_from_db()
        self.assertEqual(approval.content_hash, stored_hash)
        self.assertNotEqual(
            _v2_payload(counselor, status["context"])["counseling_minutes"],
            historical_payload["counseling_minutes"],
        )

        client = HttpClient()
        client.force_login(admin)
        admin_approved = client.post(
            reverse(
                "admin_panel:enrollment_training_certificate_approve",
                kwargs={"enrollment_id": first.pk},
            )
        )
        self.assertEqual(admin_approved.status_code, 302)
        created = TrainingCertificateApproval.objects.exclude(pk=approval.pk).get()
        self.assertEqual(created.training_enrollment_id, first.pk)
        self.assertNotEqual(created.training_enrollment_id, second.pk)

        client.force_login(outsider)
        denied = client.post(
            reverse(
                "supervisor:enrollment_training_certificate_approve",
                kwargs={"enrollment_id": first.pk},
            )
        )
        self.assertEqual(denied.status_code, 403)
        client.force_login(supervisor)
        allowed = client.post(
            reverse(
                "supervisor:enrollment_training_certificate_approve",
                kwargs={"enrollment_id": first.pk},
            )
        )
        self.assertEqual(allowed.status_code, 302)
        created = TrainingCertificateApproval.objects.exclude(pk=approval.pk).get()
        self.assertEqual(created.training_enrollment_id, first.pk)
        self.assertNotEqual(created.training_enrollment_id, second.pk)
        approval.refresh_from_db()
        self.assertEqual(approval.content_hash, stored_hash)
        self.assertEqual(
            get_training_certificate_approval_status(
                counselor, training_enrollment=second
            )["status"],
            STATUS_UNAPPROVED,
        )

        client.force_login(admin)
        page = client.get(
            reverse(
                "admin_panel:enrollment_training_progress_detail",
                kwargs={"enrollment_id": first.pk},
            )
        )
        self.assertContains(page, "승인완료")
        self.assertContains(page, "변경관리")
        self.assertContains(page, "승인일:")
        self.assertContains(
            page,
            reverse(
                "admin_panel:enrollment_training_certificate",
                kwargs={"enrollment_id": first.pk},
            ),
        )
        self.assertContains(page, "(총 1개 사례 중 최종 종결 처리 1건)")
        self.assertNotContains(
            page,
            reverse(
                "admin_panel:enrollment_training_certificate_approve",
                kwargs={"enrollment_id": second.pk},
            ),
        )


class CohortPdfIndependenceTests(TestCase):
    def test_revoking_cohort2_keeps_cohort1_pdf(self):
        counselor = _counselor("분리A", 2)
        _supervisor("김분리", [1])
        admin = _user("분리관리@v2.example.com", "분리관리", UserRole.ADMIN)
        first = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.COMPLETED,
        )
        second = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=2,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        _case(counselor, first, "SEP-1", journals=1)
        _case(counselor, second, "SEP-2", journals=2)
        approve_training_certificate(counselor, admin, first)
        approve_training_certificate(counselor, admin, second)
        self.assertEqual(TrainingCertificateApproval.objects.count(), 2)

        client = HttpClient()
        client.force_login(admin)
        first_pdf = reverse(
            "admin_panel:enrollment_training_certificate",
            kwargs={"enrollment_id": first.pk},
        )
        second_pdf = reverse(
            "admin_panel:enrollment_training_certificate",
            kwargs={"enrollment_id": second.pk},
        )
        self.assertEqual(client.get(first_pdf).status_code, 200)
        self.assertEqual(client.get(second_pdf).status_code, 200)

        revoke_training_certificate(counselor, admin, training_enrollment=second)
        self.assertEqual(
            get_training_certificate_approval_status(
                counselor, training_enrollment=first
            )["status"],
            STATUS_APPROVED,
        )
        self.assertEqual(
            get_training_certificate_approval_status(
                counselor, training_enrollment=second
            )["status"],
            STATUS_UNAPPROVED,
        )
        self.assertEqual(client.get(first_pdf).status_code, 200)
        self.assertEqual(client.get(second_pdf).status_code, 403)
        self.assertEqual(TrainingCertificateApproval.objects.count(), 2)
        self.assertEqual(TrainingCertificateApprovalRevocation.objects.count(), 1)

        approve_training_certificate(counselor, admin, second)
        self.assertEqual(client.get(first_pdf).status_code, 200)
        self.assertEqual(client.get(second_pdf).status_code, 200)
        self.assertEqual(TrainingCertificateApproval.objects.count(), 3)
        self.assertEqual(TrainingCertificateApprovalRevocation.objects.count(), 1)
