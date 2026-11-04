"""수련확인증 실적·승인·PDF는 TrainingEnrollment마다 분리된다."""

from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
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


def _user(email, name, role):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


def _counselor():
    user = _user("multi@cert.example.com", "다기수", UserRole.COUNSELOR)
    profile = user.counselor_profile
    profile.cohort = 2
    profile.is_approved = True
    profile.save(update_fields=["cohort", "is_approved", "updated_at"])
    return user


def _supervisor(cohorts):
    user = _user(f"sup-{cohorts[0]}@cert.example.com", f"{cohorts[0]}기 수퍼바이저", UserRole.SUPERVISOR)
    profile = user.supervisor_profile
    profile.assigned_cohorts = cohorts
    profile.save(update_fields=["assigned_cohorts", "updated_at"])
    return user


def _case(counselor, enrollment, number, journals):
    client = _user(f"client-{number}@cert.example.com", f"내담자{number}", UserRole.CLIENT)
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


def _supervision(counselor, cohort, minutes):
    session = SupervisionSession.objects.create(
        cohort=cohort,
        session_number=cohort,
        scheduled_at=timezone.now(),
        status=SupervisionSessionStatus.COMPLETED,
        duration_minutes=minutes,
        counseling_method=CounselingMethod.IN_PERSON,
    )
    SupervisionAttendance.objects.create(
        session=session,
        counselor=counselor.counselor_profile,
        status=SupervisionAttendanceStatus.PRESENT,
    )


def _minutes(context, prefix):
    progress = context["progress"]
    return (
        progress[f"{prefix}_in_person_minutes"]
        + progress[f"{prefix}_remote_minutes"]
        + progress[f"{prefix}_unknown_minutes"]
    )


class EnrollmentCertificateIsolationTests(TestCase):
    def setUp(self):
        self.counselor = _counselor()
        self.first = TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.COMPLETED,
        )
        self.second = TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=2,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        _case(self.counselor, self.first, "C-1", journals=1)
        _case(self.counselor, self.second, "C-2", journals=5)
        _supervision(self.counselor, 1, 90)
        _supervision(self.counselor, 2, 30)
        self.admin = _user("admin@cert.example.com", "관리자", UserRole.ADMIN)

    def _context(self, enrollment):
        return build_counselor_training_certificate_context(self.counselor, enrollment)

    def test_certificate_figures_stay_inside_each_enrollment(self):
        first = self._context(self.first)
        second = self._context(self.second)
        self.assertEqual(_minutes(first, "counseling"), 60)
        self.assertEqual(_minutes(second, "counseling"), 300)
        self.assertEqual(_minutes(first, "supervision"), 90)
        self.assertEqual(_minutes(second, "supervision"), 30)
        self.assertEqual(first["counseling_cases_5plus_count"], 0)
        self.assertEqual(second["counseling_cases_5plus_count"], 1)
        self.assertEqual(
            build_training_certificate_approval_payload(self.counselor, first)["schema"],
            "certificate-metrics-v3",
        )
        self.assertEqual(
            build_training_certificate_approval_payload(self.counselor, first)[
                "training_enrollment_id"
            ],
            str(self.first.pk),
        )

    def test_profile_cohort_does_not_change_completed_enrollment_certificate(self):
        before = calculate_training_certificate_hash(self.counselor, self._context(self.first))
        profile = self.counselor.counselor_profile
        profile.cohort = 9
        profile.save(update_fields=["cohort", "updated_at"])
        after = calculate_training_certificate_hash(self.counselor, self._context(self.first))
        self.assertEqual(before, after)
        self.assertEqual(_minutes(self._context(self.first), "supervision"), 90)

    def test_approving_one_enrollment_does_not_approve_the_other(self):
        approve_training_certificate(self.counselor, self.admin, self.first)
        first = get_training_certificate_approval_status(
            self.counselor, training_enrollment=self.first
        )
        second = get_training_certificate_approval_status(
            self.counselor, training_enrollment=self.second
        )
        self.assertEqual(first["status"], STATUS_APPROVED)
        self.assertEqual(second["status"], STATUS_UNAPPROVED)
        self.assertFalse(second["can_issue"])
        self.assertEqual(first["submitted_case_count"], 1)
        self.assertEqual(second["submitted_case_count"], 1)

    def test_revoking_one_enrollment_leaves_the_other_approval(self):
        approve_training_certificate(self.counselor, self.admin, self.first)
        approve_training_certificate(self.counselor, self.admin, self.second)
        revoke_training_certificate(self.counselor, self.admin, training_enrollment=self.first)
        first = get_training_certificate_approval_status(
            self.counselor, training_enrollment=self.first
        )
        second = get_training_certificate_approval_status(
            self.counselor, training_enrollment=self.second
        )
        self.assertEqual(first["status"], STATUS_UNAPPROVED)
        self.assertEqual(second["status"], STATUS_APPROVED)

    def test_scoped_v2_hash_stays_approved_until_that_row_is_revoked(self):
        scoped = self._context(self.first)
        legacy_context = dict(scoped)
        legacy_context.pop("training_enrollment")
        legacy_hash = calculate_training_certificate_hash(self.counselor, legacy_context)
        TrainingCertificateApproval.objects.create(
            counselor=self.counselor,
            training_enrollment=self.first,
            approved_by=self.admin,
            approved_role=UserRole.ADMIN,
            content_hash=legacy_hash,
        )
        status = get_training_certificate_approval_status(
            self.counselor, training_enrollment=self.first
        )
        self.assertEqual(status["status"], STATUS_APPROVED)
        self.assertNotEqual(status["content_hash"], legacy_hash)
        current_hash = status["content_hash"]
        TrainingCertificateApproval.objects.create(
            counselor=self.counselor,
            training_enrollment=self.first,
            approved_by=self.admin,
            approved_role=UserRole.ADMIN,
            content_hash=current_hash,
        )
        newer = TrainingCertificateApproval.objects.filter(
            training_enrollment=self.first,
            content_hash=current_hash,
        ).get()
        TrainingCertificateApprovalRevocation.objects.create(
            approval=newer,
            revoked_by=self.admin,
            revoked_role=UserRole.ADMIN,
        )
        after = get_training_certificate_approval_status(
            self.counselor, training_enrollment=self.first
        )
        self.assertEqual(after["status"], STATUS_UNAPPROVED)
        self.assertEqual(
            get_training_certificate_approval_status(
                self.counselor, training_enrollment=self.second
            )["status"],
            STATUS_UNAPPROVED,
        )

    def test_supervisor_can_approve_only_the_assigned_cohort(self):
        cohort_one = _supervisor([1])
        cohort_two = _supervisor([2])
        client = HttpClient()
        client.force_login(cohort_one)
        denied = client.post(
            reverse(
                "supervisor:enrollment_training_certificate_approve",
                kwargs={"enrollment_id": self.second.pk},
            )
        )
        self.assertEqual(denied.status_code, 403)
        allowed = client.post(
            reverse(
                "supervisor:enrollment_training_certificate_approve",
                kwargs={"enrollment_id": self.first.pk},
            )
        )
        self.assertEqual(allowed.status_code, 302)
        client.force_login(cohort_two)
        other = client.get(
            reverse(
                "supervisor:enrollment_training_certificate",
                kwargs={"enrollment_id": self.first.pk},
            )
        )
        self.assertEqual(other.status_code, 403)

    def test_counselor_pdf_uses_the_requested_enrollment(self):
        approve_training_certificate(self.counselor, self.admin, self.first)
        client = HttpClient()
        client.force_login(self.counselor)
        issued = client.get(
            reverse("counselor:training_certificate"),
            {"enrollment": str(self.first.pk)},
        )
        self.assertEqual(issued.status_code, 200)
        self.assertEqual(issued["Content-Type"], "application/pdf")
        blocked = client.get(
            reverse("counselor:training_certificate"),
            {"enrollment": str(self.second.pk)},
        )
        self.assertEqual(blocked.status_code, 403)
        stranger = _user("other@cert.example.com", "다른상담사", UserRole.COUNSELOR)
        foreign = TrainingEnrollment.objects.create(
            counselor=stranger,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        missing = client.get(
            reverse("counselor:training_certificate"),
            {"enrollment": str(foreign.pk)},
        )
        self.assertEqual(missing.status_code, 404)

    def test_detail_card_posts_to_that_enrollment(self):
        client = HttpClient()
        client.force_login(self.admin)
        page = client.get(
            reverse(
                "admin_panel:enrollment_training_progress_detail",
                kwargs={"enrollment_id": self.first.pk},
            )
        )
        self.assertContains(
            page,
            reverse(
                "admin_panel:enrollment_training_certificate_approve",
                kwargs={"enrollment_id": self.first.pk},
            ),
        )
        self.assertNotContains(
            page,
            reverse(
                "admin_panel:enrollment_training_certificate_approve",
                kwargs={"enrollment_id": self.second.pk},
            ),
        )
