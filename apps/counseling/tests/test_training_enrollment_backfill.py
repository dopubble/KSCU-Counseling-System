"""TrainingEnrollment backfill은 기수가 분명한 상담사만 연결한다."""

from importlib import import_module

from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, migrations, transaction
from django.test import TestCase
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
    build_counselor_training_certificate_context,
    build_training_certificate_approval_payload,
    calculate_training_certificate_hash,
)

backfill_training_enrollments = import_module(
    "apps.counseling.migrations.0067_backfill_training_enrollments"
).backfill_training_enrollments


def _user(email, name, role):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


def _counselor(name, cohort):
    user = _user(f"{name}@enroll.example.com", name, UserRole.COUNSELOR)
    profile = user.counselor_profile
    profile.cohort = cohort
    profile.is_approved = cohort is not None
    profile.save(update_fields=["cohort", "is_approved", "updated_at"])
    return user


def _client(name):
    return _user(f"{name}@client.example.com", name, UserRole.CLIENT)


def _case(counselor, number):
    client = _client(number)
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
        case_number=number,
        counseling_method=CounselingMethod.IN_PERSON,
        total_sessions=10,
        remaining_sessions=10,
        status=CaseStatus.ACTIVE,
    )


def _attendance(profile, cohort, number, *, status=SupervisionSessionStatus.COMPLETED):
    session = SupervisionSession.objects.create(
        cohort=cohort,
        session_number=number,
        scheduled_at=timezone.now(),
        status=status,
    )
    return SupervisionAttendance.objects.create(
        session=session,
        counselor=profile,
        status=SupervisionAttendanceStatus.PRESENT,
    )


def _post(author, cohort, title):
    return CasePresentationPost.objects.create(
        cohort=cohort,
        author=author,
        title=title,
        file=SimpleUploadedFile(f"{title}.pdf", b"%PDF-1.4"),
    )


def _approval(counselor, digest):
    return TrainingCertificateApproval.objects.create(
        counselor=counselor,
        content_hash=digest,
        approved_role=UserRole.ADMIN,
    )


def _run_backfill():
    backfill_training_enrollments(apps, None)


class TrainingEnrollmentConstraintTests(TestCase):
    def test_one_active_enrollment_per_counselor_and_one_row_per_cohort(self):
        counselor = _counselor("제약", 1)
        TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.COMPLETED,
        )
        TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=2,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TrainingEnrollment.objects.create(
                    counselor=counselor,
                    cohort=1,
                    status=TrainingEnrollmentStatus.WITHDRAWN,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TrainingEnrollment.objects.create(
                    counselor=counselor,
                    cohort=3,
                    status=TrainingEnrollmentStatus.ACTIVE,
                )


class TrainingEnrollmentBackfillTests(TestCase):
    def test_single_cohort_case_is_linked(self):
        counselor = _counselor("사례연결", 4)
        case = _case(counselor, "ENR-A")
        _run_backfill()
        case.refresh_from_db()
        enrollment = TrainingEnrollment.objects.get(counselor=counselor)
        self.assertEqual(enrollment.cohort, 4)
        self.assertEqual(enrollment.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertIsNone(enrollment.started_at)
        self.assertIsNone(enrollment.completed_at)
        self.assertEqual(case.training_enrollment_id, enrollment.pk)
        self.assertEqual(case.counselor_id, counselor.pk)
        self.assertEqual(counselor.counselor_profile.cohort, 4)

    def test_single_cohort_approval_is_linked_without_changing_the_row(self):
        counselor = _counselor("승인연결", 4)
        approval = _approval(counselor, "b" * 64)
        before = (
            approval.content_hash,
            approval.approved_by_id,
            approval.approved_role,
            approval.approved_at,
        )
        _run_backfill()
        approval.refresh_from_db()
        enrollment = TrainingEnrollment.objects.get(counselor=counselor)
        self.assertEqual(approval.training_enrollment_id, enrollment.pk)
        self.assertEqual(
            (
                approval.content_hash,
                approval.approved_by_id,
                approval.approved_role,
                approval.approved_at,
            ),
            before,
        )

    def test_case_and_approval_share_the_same_enrollment(self):
        counselor = _counselor("동시연결", 5)
        case = _case(counselor, "ENR-C")
        approval = _approval(counselor, "c" * 64)
        _attendance(counselor.counselor_profile, 5, 1)
        _post(counselor, 5, "같은기수")
        _run_backfill()
        _run_backfill()
        case.refresh_from_db()
        approval.refresh_from_db()
        self.assertEqual(TrainingEnrollment.objects.filter(counselor=counselor).count(), 1)
        self.assertEqual(case.training_enrollment_id, approval.training_enrollment_id)
        self.assertEqual(case.training_enrollment.cohort, 5)

    def test_different_supervision_cohort_is_not_backfilled(self):
        counselor = _counselor("수퍼충돌", 1)
        case = _case(counselor, "ENR-D")
        _attendance(counselor.counselor_profile, 2, 1)
        _run_backfill()
        case.refresh_from_db()
        self.assertFalse(TrainingEnrollment.objects.filter(counselor=counselor).exists())
        self.assertIsNone(case.training_enrollment_id)

    def test_different_post_cohort_is_not_backfilled(self):
        counselor = _counselor("게시충돌", 1)
        case = _case(counselor, "ENR-E")
        _post(counselor, 2, "다른기수")
        _run_backfill()
        case.refresh_from_db()
        self.assertFalse(TrainingEnrollment.objects.filter(counselor=counselor).exists())
        self.assertIsNone(case.training_enrollment_id)

    def test_multiple_supervision_cohorts_are_not_backfilled(self):
        counselor = _counselor("수퍼여러", 1)
        _attendance(counselor.counselor_profile, 1, 1)
        _attendance(counselor.counselor_profile, 2, 2)
        _run_backfill()
        self.assertFalse(TrainingEnrollment.objects.filter(counselor=counselor).exists())

    def test_multiple_post_cohorts_are_not_backfilled(self):
        counselor = _counselor("게시여러", 1)
        _post(counselor, 1, "일기")
        _post(counselor, 2, "이기")
        _run_backfill()
        self.assertFalse(TrainingEnrollment.objects.filter(counselor=counselor).exists())

    def test_cohort_without_training_records_still_gets_an_enrollment(self):
        counselor = _counselor("실적없음", 7)
        _run_backfill()
        enrollment = TrainingEnrollment.objects.get(counselor=counselor)
        self.assertEqual(enrollment.cohort, 7)
        self.assertEqual(enrollment.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(enrollment.cases.count(), 0)
        self.assertEqual(enrollment.certificate_approvals.count(), 0)

    def test_missing_profile_cohort_is_not_backfilled(self):
        counselor = _counselor("기수없음", None)
        case = _case(counselor, "ENR-I")
        _run_backfill()
        case.refresh_from_db()
        self.assertFalse(TrainingEnrollment.objects.filter(counselor=counselor).exists())
        self.assertIsNone(case.training_enrollment_id)

    def test_cancelled_session_in_another_cohort_does_not_block_backfill(self):
        counselor = _counselor("취소회차", 3)
        _attendance(
            counselor.counselor_profile,
            9,
            1,
            status=SupervisionSessionStatus.CANCELLED,
        )
        case = _case(counselor, "ENR-X")
        _run_backfill()
        case.refresh_from_db()
        enrollment = TrainingEnrollment.objects.get(counselor=counselor, cohort=3)
        self.assertEqual(case.training_enrollment_id, enrollment.pk)

    def test_existing_enrollment_link_is_not_replaced(self):
        counselor = _counselor("기존연결", 4)
        enrollment = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=4,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        linked = _case(counselor, "ENR-KEEP")
        linked.training_enrollment = enrollment
        linked.save(update_fields=["training_enrollment"])
        empty = _case(counselor, "ENR-FILL")
        _run_backfill()
        linked.refresh_from_db()
        empty.refresh_from_db()
        self.assertEqual(TrainingEnrollment.objects.filter(counselor=counselor).count(), 1)
        self.assertEqual(linked.training_enrollment_id, enrollment.pk)
        self.assertEqual(empty.training_enrollment_id, enrollment.pk)

    def test_backfill_keeps_v2_hash_and_revocation(self):
        counselor = _counselor("해시유지", 6)
        _case(counselor, "ENR-HASH")
        before_context = build_counselor_training_certificate_context(counselor)
        before_hash = calculate_training_certificate_hash(counselor, before_context)
        before_payload = build_training_certificate_approval_payload(counselor, before_context)
        approval = TrainingCertificateApproval.objects.create(
            counselor=counselor,
            content_hash=before_hash,
            approved_role=UserRole.ADMIN,
        )
        admin = _user("해시관리@enroll.example.com", "해시관리", UserRole.ADMIN)
        revocation = TrainingCertificateApprovalRevocation.objects.create(
            approval=approval,
            revoked_by=admin,
            revoked_role=UserRole.ADMIN,
            reason="확인",
        )
        snapshot = (
            revocation.approval_id,
            revocation.revoked_by_id,
            revocation.revoked_role,
            revocation.revoked_at,
            revocation.reason,
        )
        _run_backfill()
        approval.refresh_from_db()
        revocation.refresh_from_db()
        after_context = build_counselor_training_certificate_context(counselor)
        after_payload = build_training_certificate_approval_payload(counselor, after_context)
        self.assertEqual(after_payload, before_payload)
        self.assertEqual(after_payload["schema"], "certificate-metrics-v2")
        self.assertNotIn("training_enrollment_id", after_payload)
        self.assertEqual(
            calculate_training_certificate_hash(counselor, after_context),
            before_hash,
        )
        self.assertEqual(approval.content_hash, before_hash)
        self.assertEqual(approval.approved_role, UserRole.ADMIN)
        self.assertIsNone(approval.approved_by_id)
        self.assertIsNotNone(approval.training_enrollment_id)
        self.assertEqual(
            (
                revocation.approval_id,
                revocation.revoked_by_id,
                revocation.revoked_role,
                revocation.revoked_at,
                revocation.reason,
            ),
            snapshot,
        )

    def test_reverse_keeps_backfilled_and_later_enrollments(self):
        counselor = _counselor("되돌림", 4)
        case = _case(counselor, "ENR-REV")
        approval = _approval(counselor, "a" * 64)
        _run_backfill()
        backfilled = TrainingEnrollment.objects.get(counselor=counselor, cohort=4)
        later = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=5,
            status=TrainingEnrollmentStatus.COMPLETED,
        )
        later_case = _case(counselor, "ENR-LATER")
        later_case.training_enrollment = later
        later_case.save(update_fields=["training_enrollment"])
        operation = import_module(
            "apps.counseling.migrations.0067_backfill_training_enrollments"
        ).Migration.operations[0]
        self.assertIs(operation.reverse_code, migrations.RunPython.noop)
        operation.reverse_code(apps, None)
        case.refresh_from_db()
        approval.refresh_from_db()
        later_case.refresh_from_db()
        self.assertTrue(TrainingEnrollment.objects.filter(pk=backfilled.pk).exists())
        self.assertTrue(TrainingEnrollment.objects.filter(pk=later.pk).exists())
        self.assertEqual(case.training_enrollment_id, backfilled.pk)
        self.assertEqual(approval.training_enrollment_id, backfilled.pk)
        self.assertEqual(later_case.training_enrollment_id, later.pk)

    def test_backfill_can_be_reapplied_after_noop_reverse_without_duplicates(self):
        counselor = _counselor("재적용", 4)
        case = _case(counselor, "ENR-REAPPLY")
        approval = _approval(counselor, "b" * 64)
        admin = _user("재적용관리@enroll.example.com", "재적용관리", UserRole.ADMIN)
        revocation = TrainingCertificateApprovalRevocation.objects.create(
            approval=approval,
            revoked_by=admin,
            revoked_role=UserRole.ADMIN,
            reason="유지",
        )
        _run_backfill()
        self.assertEqual(TrainingEnrollment.objects.filter(counselor=counselor).count(), 1)
        enrollment = TrainingEnrollment.objects.get(counselor=counselor, cohort=4)
        started = timezone.now().replace(microsecond=0)
        enrollment.status = TrainingEnrollmentStatus.COMPLETED
        enrollment.started_at = started
        enrollment.completed_at = started
        enrollment.save(update_fields=["status", "started_at", "completed_at"])
        enrollment.refresh_from_db()
        started = enrollment.started_at
        completed = enrollment.completed_at
        operation = import_module(
            "apps.counseling.migrations.0067_backfill_training_enrollments"
        ).Migration.operations[0]
        operation.reverse_code(apps, None)
        _run_backfill()
        self.assertEqual(TrainingEnrollment.objects.filter(counselor=counselor).count(), 1)
        enrollment.refresh_from_db()
        case.refresh_from_db()
        approval.refresh_from_db()
        revocation.refresh_from_db()
        self.assertEqual(enrollment.status, TrainingEnrollmentStatus.COMPLETED)
        self.assertEqual(enrollment.started_at, started)
        self.assertEqual(enrollment.completed_at, completed)
        self.assertEqual(case.training_enrollment_id, enrollment.pk)
        self.assertEqual(approval.training_enrollment_id, enrollment.pk)
        self.assertEqual(approval.content_hash, "b" * 64)
        self.assertIsNone(approval.approved_by_id)
        self.assertEqual(approval.approved_role, UserRole.ADMIN)
        self.assertEqual(revocation.reason, "유지")
        self.assertEqual(revocation.revoked_by_id, admin.pk)
