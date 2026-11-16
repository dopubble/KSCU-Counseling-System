from datetime import datetime, timedelta
import inspect
import tempfile
import threading

from django.contrib.admin.models import LogEntry
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from apps.accounts.models import CounselorProfile, SupervisorProfile, User, UserRole, UserStatus
from apps.counseling import cohort_reenrollment as cohort_reenrollment_module
from apps.counseling.cohort_reenrollment import (
    MSG_ACTIVE_LIMIT,
    MSG_CANCEL_GONE,
    MSG_CANCEL_NOT_ACTIVE,
    MSG_CANCEL_ORIGINAL,
    MSG_CONCURRENT,
    CohortReenrollmentError,
    cancel_training_enrollment,
    complete_training_enrollment,
    register_next_cohort,
)
from apps.counseling.presentation_board import user_can_create_presentation_post
from apps.counseling.training_progress import get_counselor_training_progress
from apps.counseling.journal_permissions import user_can_view_journal
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
from apps.counseling.services import assign_counselor
from apps.counseling.supervision_attendance import session_roster
from apps.counseling.training_certificate import (
    STATUS_APPROVED,
    STATUS_UNAPPROVED,
    _hash_payload,
    approve_training_certificate,
    build_counselor_training_certificate_context,
    build_training_certificate_approval_payload,
    get_training_certificate_approval_status,
)
from apps.documents.models import ConsentDocType
from apps.documents.services.consent_service import upsert_counselor_consent
from apps.reports.views import build_cohort_confirmed_method_stats
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.sessions_app.models import (
    CounselingJournal,
    InitialCounselingRecord,
    TerminationCounselingRecord,
)


def _user(email, name, role):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


def _counselor(email, name, cohort):
    user = _user(email, name, UserRole.COUNSELOR)
    CounselorProfile.objects.update_or_create(
        user=user,
        defaults={"cohort": cohort, "is_approved": True},
    )
    return user


def _client(email, name):
    return _user(email, name, UserRole.CLIENT)


def _supervisor(email, name, cohorts):
    user = _user(email, name, UserRole.SUPERVISOR)
    SupervisorProfile.objects.update_or_create(
        user=user,
        defaults={"assigned_cohorts": cohorts},
    )
    return user


def _case(counselor, client, number, enrollment, *, status=CaseStatus.CLOSED, submitted=True):
    application = CounselingApplication.objects.create(
        client=client,
        counseling_types=["개인상담"],
        reason="재등록",
        counseling_method=CounselingMethod.IN_PERSON,
        status=ApplicationStatus.CLOSED if status == CaseStatus.CLOSED else ApplicationStatus.IN_PROGRESS,
    )
    case = Case.objects.create(
        application=application,
        client=client,
        counselor=counselor,
        training_enrollment=enrollment,
        case_number=number,
        status=status,
        counseling_method=CounselingMethod.IN_PERSON,
        total_sessions=8,
        remaining_sessions=0 if status == CaseStatus.CLOSED else 8,
    )
    if submitted:
        case.records_submitted_at = timezone.now()
        case.records_submitted_by = counselor
        case.save(update_fields=["records_submitted_at", "records_submitted_by"])
    return case


def _appointment(counselor, client, case, status, when):
    return Appointment.objects.create(
        case=case,
        counselor=counselor,
        client=client,
        scheduled_at=when,
        duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
        status=status,
        confirmed_at=timezone.now() if status == AppointmentStatus.CONFIRMED else None,
    )


class CohortReenrollmentTests(TestCase):
    def setUp(self):
        self.admin = _user("reenroll-admin@example.com", "관리자", UserRole.ADMIN)
        self.counselor = _counselor("reenroll-hong@example.com", "홍길동", 1)
        self.client_user = _client("reenroll-client@example.com", "이내담")
        self.supervisor_one = _supervisor("reenroll-sv1@example.com", "김교수", [1])
        self.supervisor_two = _supervisor("reenroll-sv2@example.com", "이교수", [2])
        self.first = TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
            started_at=timezone.now() - timedelta(days=30),
        )
        self.case = _case(self.counselor, self.client_user, "REENROLL-1", self.first)
        self.journal = CounselingJournal.objects.create(
            case=self.case,
            counselor=self.counselor,
            session_number=1,
            is_draft=False,
            subjective="일기기밀",
        )
        self.initial = InitialCounselingRecord.objects.create(
            case=self.case,
            counselor=self.counselor,
            is_draft=False,
            presented_problems_summary="초기비밀",
        )
        self.termination = TerminationCounselingRecord.objects.create(
            case=self.case,
            counselor=self.counselor,
            is_draft=False,
            termination_reason="종결비밀",
        )
        self.past_appointment = _appointment(
            self.counselor,
            self.client_user,
            self.case,
            AppointmentStatus.CONFIRMED,
            timezone.now() - timedelta(days=2),
        )
        self.session_one = SupervisionSession.objects.create(
            cohort=1,
            session_number=1,
            scheduled_at=timezone.make_aware(datetime(2026, 3, 2, 10, 0)),
            duration_minutes=50,
            counseling_method=CounselingMethod.IN_PERSON,
            status=SupervisionSessionStatus.COMPLETED,
        )
        self.attendance = SupervisionAttendance.objects.create(
            session=self.session_one,
            counselor=self.counselor.counselor_profile,
            status=SupervisionAttendanceStatus.PRESENT,
            checked_at=timezone.now(),
        )
        self.session_two = SupervisionSession.objects.create(
            cohort=2,
            session_number=1,
            scheduled_at=timezone.make_aware(datetime(2026, 9, 2, 10, 0)),
            duration_minutes=50,
            counseling_method=CounselingMethod.IN_PERSON,
            status=SupervisionSessionStatus.SCHEDULED,
        )
        self.post = CasePresentationPost.objects.create(
            cohort=1,
            author=self.counselor,
            case=self.case,
            title="1기발표원문",
            file=SimpleUploadedFile("one.pdf", b"%PDF-1.4 one"),
        )
        context = build_counselor_training_certificate_context(self.counselor, self.first)
        v2_payload = build_training_certificate_approval_payload(self.counselor, context)
        v2_payload["schema"] = "certificate-metrics-v2"
        v2_payload.pop("training_enrollment_id", None)
        self.v2_approval = TrainingCertificateApproval.objects.create(
            counselor=self.counselor,
            training_enrollment=self.first,
            approved_by=self.admin,
            approved_role=UserRole.ADMIN,
            content_hash=_hash_payload(v2_payload),
        )
        self.revoked = TrainingCertificateApproval.objects.create(
            counselor=self.counselor,
            training_enrollment=self.first,
            approved_by=self.admin,
            approved_role=UserRole.ADMIN,
            content_hash="old-revoked-hash",
        )
        self.revocation = TrainingCertificateApprovalRevocation.objects.create(
            approval=self.revoked,
            revoked_by=self.admin,
            revoked_role=UserRole.ADMIN,
            reason="과거 취소",
        )
        self.v3_approval = approve_training_certificate(
            self.counselor, self.admin, training_enrollment=self.first
        )
        self.v2_hash = self.v2_approval.content_hash
        self.v2_id = self.v2_approval.pk
        self.v2_approved_at = self.v2_approval.approved_at
        self.v3_hash = self.v3_approval.content_hash
        self.v3_id = self.v3_approval.pk
        self.v3_approved_at = self.v3_approval.approved_at
        self.revocation_id = self.revocation.pk
        self.approval_count = TrainingCertificateApproval.objects.filter(
            training_enrollment=self.first
        ).count()
        self.user_id = self.counselor.pk
        self.email = self.counselor.email
        self.http = Client()
        self.url = reverse(
            "admin_panel:counselor_cohort_reenrollment",
            kwargs={"counselor_id": self.counselor.pk},
        )

    def _assert_registration_unchanged(self):
        self.first.refresh_from_db()
        self.case.refresh_from_db()
        self.v2_approval.refresh_from_db()
        self.v3_approval.refresh_from_db()
        self.revocation.refresh_from_db()
        self.counselor.refresh_from_db()
        self.counselor.counselor_profile.refresh_from_db()
        self.assertEqual(self.counselor.pk, self.user_id)
        self.assertEqual(self.counselor.email, self.email)
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertIsNone(self.first.completed_at)
        self.assertEqual(self.counselor.counselor_profile.cohort, 1)
        self.assertFalse(
            TrainingEnrollment.objects.filter(counselor=self.counselor, cohort=2).exists()
        )
        self.assertEqual(self.case.training_enrollment_id, self.first.pk)
        self.assertEqual(self.v2_approval.pk, self.v2_id)
        self.assertEqual(self.v2_approval.content_hash, self.v2_hash)
        self.assertEqual(self.v2_approval.approved_at, self.v2_approved_at)
        self.assertEqual(self.v2_approval.approved_by_id, self.admin.pk)
        self.assertEqual(self.v3_approval.pk, self.v3_id)
        self.assertEqual(self.v3_approval.content_hash, self.v3_hash)
        self.assertEqual(self.v3_approval.approved_at, self.v3_approved_at)
        self.assertEqual(self.v3_approval.approved_by_id, self.admin.pk)
        self.assertEqual(self.revocation.pk, self.revocation_id)
        self.assertEqual(self.revocation.reason, "과거 취소")
        self.assertEqual(
            TrainingCertificateApproval.objects.filter(
                training_enrollment=self.first
            ).count(),
            self.approval_count,
        )

    def _post(self, cohort="2", user=None):
        self.http.login(
            email=(user or self.admin).email,
            password="pass12345",
        )
        return self.http.post(self.url, {"cohort": str(cohort)}, follow=True)

    def test_admin_registers_higher_cohort_and_keeps_history(self):
        response = self._post("2")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2기 수련 등록이 완료되었습니다.")

        self.counselor.refresh_from_db()
        self.first.refresh_from_db()
        self.case.refresh_from_db()
        self.journal.refresh_from_db()
        self.initial.refresh_from_db()
        self.termination.refresh_from_db()
        self.attendance.refresh_from_db()
        self.post.refresh_from_db()
        self.v2_approval.refresh_from_db()
        self.v3_approval.refresh_from_db()
        self.revocation.refresh_from_db()
        profile = self.counselor.counselor_profile
        profile.refresh_from_db()
        second = TrainingEnrollment.objects.get(counselor=self.counselor, cohort=2)

        self.assertEqual(self.counselor.email, "reenroll-hong@example.com")
        self.assertEqual(profile.cohort, 1)
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertIsNone(self.first.completed_at)
        self.assertEqual(second.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertIsNotNone(second.started_at)
        self.assertIsNone(second.completed_at)
        self.assertEqual(
            TrainingEnrollment.objects.filter(
                counselor=self.counselor, status=TrainingEnrollmentStatus.ACTIVE
            ).count(),
            2,
        )
        self.assertEqual(self.case.training_enrollment_id, self.first.pk)
        self.assertEqual(self.case.counselor_id, self.counselor.pk)
        self.assertEqual(self.journal.subjective, "일기기밀")
        self.assertEqual(self.initial.presented_problems_summary, "초기비밀")
        self.assertEqual(self.termination.termination_reason, "종결비밀")
        self.assertEqual(self.attendance.status, SupervisionAttendanceStatus.PRESENT)
        self.assertEqual(self.post.cohort, 1)
        self.assertEqual(self.v2_approval.content_hash, self.v2_hash)
        self.assertEqual(self.v2_approval.training_enrollment_id, self.first.pk)
        self.assertEqual(self.v2_approval.approved_by_id, self.admin.pk)
        self.assertEqual(self.v3_approval.content_hash, self.v3_hash)
        self.assertEqual(self.v3_approval.approved_at, self.v3_approved_at)
        self.assertEqual(self.v3_approval.approved_role, UserRole.ADMIN)
        self.assertEqual(self.v3_approval.training_enrollment_id, self.first.pk)
        self.assertEqual(self.revocation.reason, "과거 취소")

        first_status = get_training_certificate_approval_status(
            self.counselor, training_enrollment=self.first
        )
        second_status = get_training_certificate_approval_status(
            self.counselor, training_enrollment=second
        )
        self.assertEqual(first_status["status"], STATUS_APPROVED)
        self.assertTrue(first_status["can_issue"])
        self.assertEqual(second_status["status"], STATUS_UNAPPROVED)
        self.assertFalse(second_status["can_issue"])

        self.http.login(email=self.admin.email, password="pass12345")
        old_pdf = self.http.get(
            reverse(
                "admin_panel:enrollment_training_certificate",
                kwargs={"enrollment_id": self.first.pk},
            )
        )
        new_pdf = self.http.get(
            reverse(
                "admin_panel:enrollment_training_certificate",
                kwargs={"enrollment_id": second.pk},
            )
        )
        self.assertEqual(old_pdf.status_code, 200)
        self.assertEqual(new_pdf.status_code, 403)

        progress_one = self.http.get(reverse("admin_panel:training_progress"), {"cohort": "1"})
        progress_two = self.http.get(reverse("admin_panel:training_progress"), {"cohort": "2"})
        self.assertContains(progress_one, "홍길동")
        self.assertContains(progress_two, "홍길동")

        rows, totals = build_cohort_confirmed_method_stats()
        cohort_one = next(row for row in rows if row["cohort"] == 1)
        self.assertEqual(cohort_one["total_count"], 1)
        self.assertEqual(totals["total_count"], 1)
        self.assertFalse(any(row["cohort"] == 2 for row in rows))

        roster = session_roster(self.session_two)
        self.assertIn(self.counselor.pk, [item["profile"].user_id for item in roster])
        past = session_roster(self.session_one)
        past_row = next(item for item in past if item["profile"].user_id == self.counselor.pk)
        self.assertEqual(past_row["status"], SupervisionAttendanceStatus.PRESENT)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.session_id, self.session_one.pk)

        self.assertTrue(user_can_view_journal(self.supervisor_one, self.journal))
        self.assertFalse(user_can_view_journal(self.supervisor_two, self.journal))

        self.http.login(email=self.counselor.email, password="pass12345")
        dashboard = self.http.get(reverse("counselor:dashboard"))
        self.assertContains(dashboard, "1기 / 진행 중")
        self.assertContains(dashboard, "2기 / 진행 중")
        self.assertContains(dashboard, "REENROLL-1")
        cohort_two_dash = self.http.get(
            reverse("counselor:dashboard"), {"enrollment": str(second.pk)}
        )
        self.assertNotContains(cohort_two_dash, "REENROLL-1")
        cohort_one_dash = self.http.get(
            reverse("counselor:dashboard"), {"enrollment": str(self.first.pk)}
        )
        self.assertContains(cohort_one_dash, "REENROLL-1")
        board_one = self.http.get(reverse("counselor:presentation_board"), {"cohort": "1"})
        board_two = self.http.get(reverse("counselor:presentation_board"), {"cohort": "2"})
        self.assertContains(board_one, "1기발표원문")
        self.assertNotContains(board_two, "1기발표원문")

        later_client = _client("reenroll-later@example.com", "박내담")
        application = CounselingApplication.objects.create(
            client=later_client,
            counseling_types=["개인상담"],
            reason="2기 배정",
            counseling_method=CounselingMethod.REMOTE,
            status=ApplicationStatus.WAITING_MATCH,
        )
        later_case = assign_counselor(
            application, self.counselor, total_sessions=4, training_enrollment=second
        )
        self.assertEqual(later_case.training_enrollment_id, second.pk)
        self.case.refresh_from_db()
        self.assertEqual(self.case.training_enrollment_id, self.first.pk)
        later_journal = CounselingJournal.objects.create(
            case=later_case,
            counselor=self.counselor,
            session_number=1,
            is_draft=False,
            subjective="2기기밀",
        )
        self.assertTrue(user_can_view_journal(self.supervisor_two, later_journal))
        self.assertFalse(user_can_view_journal(self.supervisor_one, later_journal))

        later_case.status = CaseStatus.CLOSED
        later_case.records_submitted_at = timezone.now()
        later_case.save(update_fields=["status", "records_submitted_at"])
        blocked = self._post("4")
        self.assertContains(blocked, MSG_ACTIVE_LIMIT)
        self.assertEqual(
            TrainingEnrollment.objects.filter(counselor=self.counselor).count(),
            2,
        )
        complete_training_enrollment(self.first)
        third = self._post("4")
        self.assertContains(third, "4기 수련 등록이 완료되었습니다.")
        self.assertEqual(
            TrainingEnrollment.objects.filter(counselor=self.counselor).count(),
            3,
        )
        self.first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.COMPLETED)
        self.assertEqual(second.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(
            TrainingEnrollment.objects.get(counselor=self.counselor, cohort=4).status,
            TrainingEnrollmentStatus.ACTIVE,
        )
        later_case.refresh_from_db()
        self.assertEqual(later_case.training_enrollment_id, second.pk)

        self.http.login(email=self.admin.email, password="pass12345")
        roster_page = self.http.get(reverse("admin_panel:counselor_list"), {"search": "홍길동"})
        self.assertContains(roster_page, "홍길동")
        self.assertEqual(roster_page.content.decode().count("새 기수 등록"), 1)
        self.assertContains(roster_page, "4기")
        self.assertEqual(CounselorProfile.objects.filter(user=self.counselor).count(), 1)

    def test_get_does_not_register_and_outsiders_are_blocked(self):
        self.http.login(email=self.admin.email, password="pass12345")
        page = self.http.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "새 기수 등록")
        self.assertContains(page, "홍길동")
        self.assertContains(page, "전체 1건")
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertFalse(
            TrainingEnrollment.objects.filter(counselor=self.counselor, cohort=2).exists()
        )

        self.http.login(email=self.counselor.email, password="pass12345")
        self.assertEqual(self.http.post(self.url, {"cohort": "2"}).status_code, 403)
        self.http.login(email=self.supervisor_one.email, password="pass12345")
        self.assertEqual(self.http.post(self.url, {"cohort": "2"}).status_code, 403)
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)

        guarded = Client(enforce_csrf_checks=True)
        guarded.login(email=self.admin.email, password="pass12345")
        self.assertEqual(guarded.post(self.url, {"cohort": "2"}).status_code, 403)

    def test_open_unsubmitted_and_upcoming_work_does_not_block(self):
        self.case.status = CaseStatus.ACTIVE
        self.case.records_submitted_at = None
        self.case.records_submitted_by = None
        self.case.save(update_fields=["status", "records_submitted_at", "records_submitted_by"])
        upcoming = _appointment(
            self.counselor,
            self.client_user,
            self.case,
            AppointmentStatus.CONFIRMED,
            timezone.now() + timedelta(days=3),
        )
        response = self._post("2")
        self.assertContains(response, "2기 수련 등록이 완료되었습니다.")
        self.first.refresh_from_db()
        self.case.refresh_from_db()
        upcoming.refresh_from_db()
        self.counselor.counselor_profile.refresh_from_db()
        second = TrainingEnrollment.objects.get(counselor=self.counselor, cohort=2)
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(second.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(self.counselor.counselor_profile.cohort, 1)
        self.assertEqual(self.case.status, CaseStatus.ACTIVE)
        self.assertIsNone(self.case.records_submitted_at)
        self.assertEqual(self.case.training_enrollment_id, self.first.pk)
        self.assertEqual(upcoming.status, AppointmentStatus.CONFIRMED)

    def test_closed_unsubmitted_case_can_register_without_submitting(self):
        self.case.records_submitted_at = None
        self.case.records_submitted_by = None
        self.case.save(update_fields=["records_submitted_at", "records_submitted_by"])
        response = self._post("3")
        self.assertContains(response, "3기 수련 등록이 완료되었습니다.")
        self.first.refresh_from_db()
        self.case.refresh_from_db()
        self.journal.refresh_from_db()
        self.termination.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertIsNone(self.case.records_submitted_at)
        self.assertEqual(self.case.training_enrollment_id, self.first.pk)
        self.assertEqual(self.journal.subjective, "일기기밀")
        self.assertEqual(self.termination.termination_reason, "종결비밀")

    def test_duplicate_and_lower_cohort_are_rejected(self):
        duplicate = self._post("1")
        self.assertContains(duplicate, "이미 등록된 기수입니다.")
        self._assert_registration_unchanged()
        invalid_text = self._post("기수")
        self.assertContains(invalid_text, "1 이상의 정수")
        self._assert_registration_unchanged()

        completed_at = timezone.now()
        self.first.status = TrainingEnrollmentStatus.COMPLETED
        self.first.completed_at = completed_at
        self.first.save(update_fields=["status", "completed_at", "updated_at"])
        TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=3,
            status=TrainingEnrollmentStatus.ACTIVE,
            started_at=timezone.now(),
        )
        lower = self._post("2")
        self.assertContains(lower, "가장 높은 기수보다 높아야")
        self.assertFalse(
            TrainingEnrollment.objects.filter(counselor=self.counselor, cohort=2).exists()
        )
        invalid = self._post("0")
        self.assertContains(invalid, "1 이상의 정수")
        self.first.refresh_from_db()
        self.case.refresh_from_db()
        self.v3_approval.refresh_from_db()
        self.counselor.counselor_profile.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.COMPLETED)
        self.assertEqual(self.first.completed_at, completed_at)
        self.assertEqual(self.counselor.counselor_profile.cohort, 1)
        self.assertEqual(self.case.training_enrollment_id, self.first.pk)
        self.assertEqual(self.v3_approval.content_hash, self.v3_hash)
        self.assertEqual(self.v3_approval.approved_at, self.v3_approved_at)
        self.assertFalse(
            TrainingEnrollment.objects.filter(counselor=self.counselor, cohort=2).exists()
        )

    def test_completed_only_and_withdrawn_history_can_add_a_higher_cohort(self):
        completed_at = timezone.now() - timedelta(days=5)
        self.first.status = TrainingEnrollmentStatus.COMPLETED
        self.first.completed_at = completed_at
        self.first.save(update_fields=["status", "completed_at", "updated_at"])
        response = self._post("3")
        self.assertContains(response, "3기 수련 등록이 완료되었습니다.")
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.COMPLETED)
        self.assertEqual(self.first.completed_at, completed_at)
        created = TrainingEnrollment.objects.get(counselor=self.counselor, cohort=3)
        self.assertEqual(created.status, TrainingEnrollmentStatus.ACTIVE)

        withdrawn_counselor = _counselor("reenroll-stop@example.com", "중단상담", 2)
        withdrawn = TrainingEnrollment.objects.create(
            counselor=withdrawn_counselor,
            cohort=2,
            status=TrainingEnrollmentStatus.WITHDRAWN,
        )
        stopped = Client()
        stopped.login(email=self.admin.email, password="pass12345")
        url = reverse(
            "admin_panel:counselor_cohort_reenrollment",
            kwargs={"counselor_id": withdrawn_counselor.pk},
        )
        denied = stopped.post(url, {"cohort": "2"}, follow=True)
        self.assertContains(denied, "이미 등록된 기수입니다.")
        allowed = stopped.post(url, {"cohort": "4"}, follow=True)
        self.assertContains(allowed, "4기 수련 등록이 완료되었습니다.")
        withdrawn.refresh_from_db()
        self.assertEqual(withdrawn.status, TrainingEnrollmentStatus.WITHDRAWN)

    def test_counselor_without_enrollment_is_not_registered_here(self):
        fresh = _counselor("reenroll-fresh@example.com", "신규상담", 1)
        self.http.login(email=self.admin.email, password="pass12345")
        listing = self.http.get(reverse("admin_panel:counselor_list"), {"search": "신규상담"})
        self.assertContains(listing, "신규상담")
        self.assertNotContains(listing, "새 기수 등록")
        url = reverse(
            "admin_panel:counselor_cohort_reenrollment",
            kwargs={"counselor_id": fresh.pk},
        )
        response = self.http.post(url, {"cohort": "2"}, follow=True)
        self.assertContains(response, "수련 등록 이력이 없는 상담사")
        self.assertFalse(TrainingEnrollment.objects.filter(counselor=fresh).exists())
        fresh.counselor_profile.refresh_from_db()
        self.assertEqual(fresh.counselor_profile.cohort, 1)

    def test_failure_inside_transaction_rolls_back(self):
        with patch(
            "apps.counseling.cohort_reenrollment.TrainingEnrollment.objects.create",
            side_effect=RuntimeError("fail"),
        ):
            with self.assertRaises(RuntimeError):
                register_next_cohort(self.counselor, "2")
        self.first.refresh_from_db()
        self.counselor.counselor_profile.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(self.counselor.counselor_profile.cohort, 1)
        self.assertFalse(
            TrainingEnrollment.objects.filter(counselor=self.counselor, cohort=2).exists()
        )

        with patch(
            "apps.counseling.cohort_reenrollment.TrainingEnrollment.objects.create",
            side_effect=IntegrityError("duplicate"),
        ):
            with self.assertRaises(CohortReenrollmentError):
                register_next_cohort(self.counselor, "2")
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)

        TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=9,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TrainingEnrollment.objects.create(
                    counselor=self.counselor,
                    cohort=10,
                    status=TrainingEnrollmentStatus.ACTIVE,
                )

    def test_admin_flow_moves_to_the_new_enrollment_detail(self):
        self.http.login(email=self.admin.email, password="pass12345")
        listing = self.http.get(
            reverse("admin_panel:counselor_list"), {"search": "홍길동"}
        )
        self.assertContains(listing, self.url)
        self.assertContains(listing, "새 기수 등록")
        confirm = self.http.get(self.url)
        self.assertContains(confirm, "신규 등록 기수")
        self.assertContains(confirm, "기존 수련기록은 삭제되지 않습니다.")
        response = self.http.post(self.url, {"cohort": "2"})
        second = TrainingEnrollment.objects.get(counselor=self.counselor, cohort=2)
        detail_url = reverse(
            "admin_panel:enrollment_training_progress_detail",
            kwargs={"enrollment_id": second.pk},
        )
        self.assertRedirects(response, detail_url, fetch_redirect_response=False)
        detail = self.http.get(detail_url)
        self.assertContains(detail, "2기 수련 등록이 완료되었습니다.")
        self.assertContains(detail, "홍길동")
        old_detail = self.http.get(
            reverse(
                "admin_panel:enrollment_training_progress_detail",
                kwargs={"enrollment_id": self.first.pk},
            )
        )
        self.assertEqual(old_detail.status_code, 200)
        self.assertContains(old_detail, "1기")
        self.counselor.refresh_from_db()
        self.first.refresh_from_db()
        self.case.refresh_from_db()
        self.assertEqual(self.counselor.pk, self.user_id)
        self.assertEqual(self.counselor.email, self.email)
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(second.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(self.counselor.counselor_profile.cohort, 1)
        self.assertEqual(self.case.training_enrollment_id, self.first.pk)

        self.http.login(email=self.counselor.email, password="pass12345")
        self.assertEqual(self.http.get(self.url).status_code, 403)

    def test_second_request_for_the_same_cohort_does_not_duplicate(self):
        first_response = self._post("2")
        self.assertContains(first_response, "2기 수련 등록이 완료되었습니다.")
        self.first.refresh_from_db()
        self.assertIsNone(self.first.completed_at)
        again = self._post("2")
        self.assertContains(again, "이미 등록된 기수입니다.")
        self.first.refresh_from_db()
        self.case.refresh_from_db()
        self.v3_approval.refresh_from_db()
        self.counselor.counselor_profile.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertIsNone(self.first.completed_at)
        self.assertEqual(self.counselor.counselor_profile.cohort, 1)
        self.assertEqual(
            TrainingEnrollment.objects.filter(counselor=self.counselor, cohort=2).count(),
            1,
        )
        self.assertEqual(self.case.training_enrollment_id, self.first.pk)
        self.assertEqual(self.v3_approval.pk, self.v3_id)
        self.assertEqual(self.v3_approval.content_hash, self.v3_hash)

    def test_waiting_application_has_no_counselor_until_assignment(self):
        waiting_client = _client("reenroll-waiting@example.com", "대기내담")
        waiting = CounselingApplication.objects.create(
            client=waiting_client,
            counseling_types=["개인상담"],
            reason="아직 배정 전",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.WAITING_MATCH,
        )
        self.assertNotIn("counselor", [field.name for field in waiting._meta.fields])
        self.assertFalse(Case.objects.filter(application=waiting).exists())
        response = self._post("2")
        self.assertContains(response, "2기 수련 등록이 완료되었습니다.")
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, ApplicationStatus.WAITING_MATCH)
        self.assertFalse(Case.objects.filter(application=waiting).exists())
        self.case.refresh_from_db()
        self.assertEqual(self.case.training_enrollment_id, self.first.pk)
        second = TrainingEnrollment.objects.get(counselor=self.counselor, cohort=2)
        created = assign_counselor(
            waiting, self.counselor, total_sessions=6, training_enrollment=second
        )
        self.assertEqual(created.training_enrollment_id, second.pk)
        self.case.refresh_from_db()
        self.assertEqual(self.case.training_enrollment_id, self.first.pk)

    def test_completed_enrollment_keeps_an_open_case_on_the_old_cohort(self):
        completed_at = timezone.now() - timedelta(days=2)
        self.first.status = TrainingEnrollmentStatus.COMPLETED
        self.first.completed_at = completed_at
        self.first.save(update_fields=["status", "completed_at", "updated_at"])
        open_client = _client("reenroll-open@example.com", "남은내담")
        open_case = _case(
            self.counselor,
            open_client,
            "REENROLL-OPEN",
            self.first,
            status=CaseStatus.ACTIVE,
            submitted=False,
        )
        response = self._post("2")
        self.assertContains(response, "2기 수련 등록이 완료되었습니다.")
        self.first.refresh_from_db()
        open_case.refresh_from_db()
        self.case.refresh_from_db()
        self.v3_approval.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.COMPLETED)
        self.assertEqual(self.first.completed_at, completed_at)
        self.assertEqual(open_case.training_enrollment_id, self.first.pk)
        self.assertEqual(open_case.status, CaseStatus.ACTIVE)
        self.assertIsNone(open_case.records_submitted_at)
        self.assertEqual(self.case.training_enrollment_id, self.first.pk)
        self.assertEqual(self.v3_approval.content_hash, self.v3_hash)
        self.assertEqual(self.v3_approval.approved_at, self.v3_approved_at)

    def test_registration_locks_the_counselor_row_when_supported(self):
        source = inspect.getsource(cohort_reenrollment_module._lock_counselor)
        self.assertIn("select_for_update", source)
        self.assertIn("User.objects.filter", source)
        self.assertIn("CounselorProfile.objects.filter", source)
        if not connection.features.has_select_for_update:
            return
        with CaptureQueriesContext(connection) as captured:
            register_next_cohort(self.counselor, "2")
        sql = " ".join(query["sql"] for query in captured.captured_queries).upper()
        self.assertIn("FOR UPDATE", sql)


class IsolatedCertificateReenrollmentTests(TestCase):
    def _ready_counselor(self, email):
        admin = _user(f"admin-{email}", "관리자", UserRole.ADMIN)
        counselor = _counselor(email, "승인상담", 1)
        client = _client(f"client-{email}", "승인내담")
        enrollment = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
            started_at=timezone.now() - timedelta(days=10),
        )
        case = _case(counselor, client, f"CERT-{email}", enrollment)
        return admin, counselor, enrollment, case

    def _assert_old_approval_survives(self, counselor, enrollment, approval):
        approval_id = approval.pk
        content_hash = approval.content_hash
        approved_at = approval.approved_at
        approved_by_id = approval.approved_by_id
        count = TrainingCertificateApproval.objects.filter(
            training_enrollment=enrollment
        ).count()
        http = Client()
        http.login(email=f"admin-{counselor.email}", password="pass12345")
        response = http.post(
            reverse(
                "admin_panel:counselor_cohort_reenrollment",
                kwargs={"counselor_id": counselor.pk},
            ),
            {"cohort": "2"},
            follow=True,
        )
        self.assertContains(response, "2기 수련 등록이 완료되었습니다.")
        approval.refresh_from_db()
        enrollment.refresh_from_db()
        second = TrainingEnrollment.objects.get(counselor=counselor, cohort=2)
        self.assertEqual(approval.pk, approval_id)
        self.assertEqual(approval.content_hash, content_hash)
        self.assertEqual(approval.approved_at, approved_at)
        self.assertEqual(approval.approved_by_id, approved_by_id)
        self.assertEqual(approval.training_enrollment_id, enrollment.pk)
        self.assertEqual(
            TrainingCertificateApproval.objects.filter(
                training_enrollment=enrollment
            ).count(),
            count,
        )
        self.assertFalse(
            TrainingCertificateApproval.objects.filter(
                training_enrollment=second
            ).exists()
        )
        self.assertEqual(enrollment.status, TrainingEnrollmentStatus.ACTIVE)
        first_status = get_training_certificate_approval_status(
            counselor, training_enrollment=enrollment
        )
        second_status = get_training_certificate_approval_status(
            counselor, training_enrollment=second
        )
        self.assertEqual(first_status["status"], STATUS_APPROVED)
        self.assertTrue(first_status["can_issue"])
        self.assertEqual(second_status["status"], STATUS_UNAPPROVED)
        self.assertFalse(second_status["can_issue"])
        http.login(email=f"admin-{counselor.email}", password="pass12345")
        old_pdf = http.get(
            reverse(
                "admin_panel:enrollment_training_certificate",
                kwargs={"enrollment_id": enrollment.pk},
            )
        )
        new_pdf = http.get(
            reverse(
                "admin_panel:enrollment_training_certificate",
                kwargs={"enrollment_id": second.pk},
            )
        )
        self.assertEqual(old_pdf.status_code, 200)
        self.assertEqual(new_pdf.status_code, 403)

    def test_v2_approval_stays_valid_after_reenrollment(self):
        admin, counselor, enrollment, _case = self._ready_counselor("v2@ex.com")
        context = build_counselor_training_certificate_context(
            counselor, enrollment, supervision_credit="duration"
        )
        payload = build_training_certificate_approval_payload(counselor, context)
        payload["schema"] = "certificate-metrics-v2"
        payload.pop("training_enrollment_id", None)
        approval = TrainingCertificateApproval.objects.create(
            counselor=counselor,
            training_enrollment=enrollment,
            approved_by=admin,
            approved_role=UserRole.ADMIN,
            content_hash=_hash_payload(payload),
        )
        self._assert_old_approval_survives(counselor, enrollment, approval)

    def test_v3_approval_stays_valid_after_reenrollment(self):
        admin, counselor, enrollment, _case = self._ready_counselor("v3@ex.com")
        approval = approve_training_certificate(
            counselor, admin, training_enrollment=enrollment
        )
        self._assert_old_approval_survives(counselor, enrollment, approval)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "consent": {
            "BACKEND": "apps.documents.storage.ConsentMediaStorage",
            "OPTIONS": {},
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    },
    MEDIA_ROOT=tempfile.mkdtemp(),
)
class CohortReenrollmentConsentTests(TestCase):
    def test_existing_consent_stays_on_the_original_case_cohort(self):
        admin = _user("consent-reenroll-admin@example.com", "관리자", UserRole.ADMIN)
        counselor = _counselor("consent-reenroll@example.com", "동의상담", 1)
        client = _client("consent-reenroll-client@example.com", "동의내담")
        enrollment = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        case = _case(counselor, client, "REENROLL-CONSENT", enrollment)
        upsert_counselor_consent(
            case=case,
            doc_type=ConsentDocType.PRIVACY,
            file_obj=SimpleUploadedFile("scan.pdf", b"%PDF-1.4", content_type="application/pdf"),
            uploaded_by=counselor,
        )
        http = Client()
        http.login(email=admin.email, password="pass12345")
        response = http.post(
            reverse(
                "admin_panel:counselor_cohort_reenrollment",
                kwargs={"counselor_id": counselor.pk},
            ),
            {"cohort": "2"},
            follow=True,
        )
        self.assertContains(response, "2기 수련 등록이 완료되었습니다.")
        http.login(email=admin.email, password="pass12345")
        consent_one = http.get(
            reverse("admin_panel:consent_submissions"),
            {"cohort": "1", "status": "all"},
        )
        consent_two = http.get(
            reverse("admin_panel:consent_submissions"),
            {"cohort": "2", "status": "all"},
        )
        self.assertContains(consent_one, "동의내담")
        self.assertNotContains(consent_two, "동의내담")
        case.refresh_from_db()
        self.assertEqual(case.training_enrollment_id, enrollment.pk)


class DualActiveCohortPolicyTests(TestCase):
    """1기 상담을 유지한 채 2기에 등록하고, 기록과 실적은 기수별로 남긴다."""

    def setUp(self):
        self.admin = _user("dual-admin@example.com", "관리자", UserRole.ADMIN)
        self.counselor = _counselor("dual-counselor@example.com", "김수련", 1)
        self.first = TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
            started_at=timezone.now() - timedelta(days=20),
        )
        self.http = Client()

    def _register(self, cohort):
        self.http.login(email=self.admin.email, password="pass12345")
        return self.http.post(
            reverse(
                "admin_panel:counselor_cohort_reenrollment",
                kwargs={"counselor_id": self.counselor.pk},
            ),
            {"cohort": str(cohort)},
            follow=True,
        )

    def test_open_cases_stay_on_cohort_one_and_journals_remain_editable(self):
        cases = []
        appointments = []
        for index in range(3):
            client = _client(f"dual-open-{index}@example.com", f"내담{index}")
            case = _case(
                self.counselor,
                client,
                f"DUAL-OPEN-{index}",
                self.first,
                status=CaseStatus.ACTIVE,
                submitted=False,
            )
            cases.append(case)
            appointments.append(
                _appointment(
                    self.counselor,
                    client,
                    case,
                    AppointmentStatus.CONFIRMED,
                    timezone.now() + timedelta(days=index + 1),
                )
            )
        response = self._register(2)
        self.assertContains(response, "2기 수련 등록이 완료되었습니다.")
        self.first.refresh_from_db()
        second = TrainingEnrollment.objects.get(counselor=self.counselor, cohort=2)
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertIsNone(self.first.completed_at)
        self.assertEqual(second.status, TrainingEnrollmentStatus.ACTIVE)
        self.counselor.counselor_profile.refresh_from_db()
        self.assertEqual(self.counselor.counselor_profile.cohort, 1)
        self.assertEqual(
            list(Case.objects.filter(training_enrollment=self.first).order_by("case_number")),
            cases,
        )
        self.assertFalse(Case.objects.filter(training_enrollment=second).exists())
        for case, appointment in zip(cases, appointments):
            case.refresh_from_db()
            appointment.refresh_from_db()
            self.assertEqual(case.status, CaseStatus.ACTIVE)
            self.assertIsNone(case.records_submitted_at)
            self.assertEqual(appointment.status, AppointmentStatus.CONFIRMED)
            self.assertEqual(appointment.case_id, case.pk)
        self.http.login(email=self.counselor.email, password="pass12345")
        journal_page = self.http.get(
            reverse("counselor:journal_create", kwargs={"pk": cases[0].pk})
        )
        self.assertEqual(journal_page.status_code, 200)

    def test_closed_unsubmitted_records_stay_editable_without_auto_submit(self):
        client = _client("dual-closed@example.com", "종결내담")
        case = _case(
            self.counselor,
            client,
            "DUAL-CLOSED",
            self.first,
            status=CaseStatus.CLOSED,
            submitted=False,
        )
        CounselingJournal.objects.create(
            case=case,
            counselor=self.counselor,
            session_number=1,
            is_draft=False,
            subjective="1기기밀",
        )
        response = self._register(2)
        self.assertContains(response, "2기 수련 등록이 완료되었습니다.")
        case.refresh_from_db()
        self.assertIsNone(case.records_submitted_at)
        self.assertIsNone(case.records_submitted_by_id)
        self.assertEqual(case.training_enrollment_id, self.first.pk)
        self.http.login(email=self.counselor.email, password="pass12345")
        self.assertEqual(
            self.http.get(reverse("counselor:journal_create", kwargs={"pk": case.pk})).status_code,
            200,
        )

    def test_records_and_training_stats_stay_on_the_selected_cohort(self):
        second = TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=2,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        first_client = _client("dual-split-1@example.com", "일기내담")
        second_client = _client("dual-split-2@example.com", "이기내담")
        first_case = assign_counselor(
            CounselingApplication.objects.create(
                client=first_client,
                counseling_types=["개인상담"],
                reason="1기",
                counseling_method=CounselingMethod.IN_PERSON,
                status=ApplicationStatus.WAITING_MATCH,
            ),
            self.counselor,
            total_sessions=4,
            training_enrollment=self.first,
        )
        second_case = assign_counselor(
            CounselingApplication.objects.create(
                client=second_client,
                counseling_types=["개인상담"],
                reason="2기",
                counseling_method=CounselingMethod.IN_PERSON,
                status=ApplicationStatus.WAITING_MATCH,
            ),
            self.counselor,
            total_sessions=4,
            training_enrollment=second,
        )
        CounselingJournal.objects.create(
            case=first_case,
            counselor=self.counselor,
            session_number=1,
            is_draft=False,
            subjective="1기기록",
        )
        CounselingJournal.objects.create(
            case=second_case,
            counselor=self.counselor,
            session_number=1,
            is_draft=False,
            subjective="2기기록",
        )
        session = SupervisionSession.objects.create(
            cohort=1,
            session_number=1,
            scheduled_at=timezone.now() - timedelta(days=1),
            duration_minutes=50,
            counseling_method=CounselingMethod.IN_PERSON,
            status=SupervisionSessionStatus.COMPLETED,
        )
        SupervisionAttendance.objects.create(
            session=session,
            counselor=self.counselor.counselor_profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )
        CasePresentationPost.objects.create(
            cohort=1,
            author=self.counselor,
            case=first_case,
            title="1기발표",
            file=SimpleUploadedFile("one.pdf", b"%PDF-1.4"),
        )
        first_progress = get_counselor_training_progress(
            self.counselor, training_enrollment=self.first
        )
        second_progress = get_counselor_training_progress(
            self.counselor, training_enrollment=second
        )
        self.assertEqual(first_case.training_enrollment_id, self.first.pk)
        self.assertEqual(second_case.training_enrollment_id, second.pk)
        self.assertEqual(first_progress["journal_completed_count"], 1)
        self.assertEqual(second_progress["journal_completed_count"], 1)
        self.assertEqual(first_progress["supervision_attended_minutes"], 50)
        self.assertEqual(second_progress["supervision_attended_minutes"], 0)
        self.assertEqual(first_progress["case_report_submitted_count"], 1)
        self.assertEqual(second_progress["case_report_submitted_count"], 0)
        self.assertTrue(user_can_create_presentation_post(self.counselor, 1))
        self.assertTrue(user_can_create_presentation_post(self.counselor, 2))
        self.http.login(email=self.counselor.email, password="pass12345")
        cohort_one = self.http.get(
            reverse("counselor:dashboard"), {"enrollment": str(self.first.pk)}
        )
        cohort_two = self.http.get(
            reverse("counselor:dashboard"), {"enrollment": str(second.pk)}
        )
        self.assertContains(cohort_one, first_case.case_number)
        self.assertNotContains(cohort_one, second_case.case_number)
        self.assertContains(cohort_two, second_case.case_number)
        self.assertNotContains(cohort_two, first_case.case_number)

    def test_completing_cohort_one_leaves_cohort_two_active(self):
        client = _client("dual-complete@example.com", "종료내담")
        case = _case(
            self.counselor,
            client,
            "DUAL-DONE",
            self.first,
            status=CaseStatus.ACTIVE,
            submitted=False,
        )
        submitted_at = case.records_submitted_at
        self._register(2)
        second = TrainingEnrollment.objects.get(counselor=self.counselor, cohort=2)
        self.http.login(email=self.admin.email, password="pass12345")
        response = self.http.post(
            reverse(
                "admin_panel:enrollment_complete",
                kwargs={"enrollment_id": self.first.pk},
            )
        )
        self.assertRedirects(
            response,
            reverse(
                "admin_panel:enrollment_training_progress_detail",
                kwargs={"enrollment_id": self.first.pk},
            ),
        )
        self.first.refresh_from_db()
        second.refresh_from_db()
        case.refresh_from_db()
        self.counselor.counselor_profile.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.COMPLETED)
        self.assertIsNotNone(self.first.completed_at)
        self.assertIsNone(self.first.active_slot)
        self.assertEqual(second.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(case.training_enrollment_id, self.first.pk)
        self.assertEqual(case.records_submitted_at, submitted_at)
        self.assertEqual(self.counselor.counselor_profile.cohort, 1)
        self.http.login(email=self.counselor.email, password="pass12345")
        dashboard = self.http.get(
            reverse("counselor:dashboard"), {"enrollment": str(second.pk)}
        )
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.context["selected_training_enrollment"], second)
        self.assertFalse(user_can_create_presentation_post(self.counselor, 1))
        self.assertTrue(user_can_create_presentation_post(self.counselor, 2))

    def test_duplicate_cohort_and_third_active_are_rejected(self):
        duplicate = self._register(1)
        self.assertContains(duplicate, "이미 등록된 기수입니다.")
        self.assertEqual(TrainingEnrollment.objects.filter(counselor=self.counselor).count(), 1)
        with patch(
            "apps.counseling.cohort_reenrollment.TrainingEnrollment.objects.create",
            side_effect=IntegrityError("slot"),
        ):
            with self.assertRaises(CohortReenrollmentError) as caught:
                register_next_cohort(self.counselor, "2")
        self.assertEqual(caught.exception.messages, [MSG_CONCURRENT])
        self.assertFalse(
            TrainingEnrollment.objects.filter(counselor=self.counselor, cohort=2).exists()
        )
        self._register(2)
        third = self._register(3)
        self.assertContains(third, MSG_ACTIVE_LIMIT)
        self.assertFalse(
            TrainingEnrollment.objects.filter(counselor=self.counselor, cohort=3).exists()
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TrainingEnrollment.objects.create(
                    counselor=self.counselor,
                    cohort=3,
                    status=TrainingEnrollmentStatus.ACTIVE,
                )
        self.assertEqual(
            TrainingEnrollment.objects.filter(
                counselor=self.counselor, status=TrainingEnrollmentStatus.ACTIVE
            ).count(),
            2,
        )

    def test_record_edit_lock_follows_records_submitted_at_only(self):
        open_client = _client("dual-lock-open@example.com", "미제출내담")
        locked_client = _client("dual-lock-closed@example.com", "제출내담")
        open_case = _case(
            self.counselor,
            open_client,
            "DUAL-UNLOCKED",
            self.first,
            status=CaseStatus.ACTIVE,
            submitted=False,
        )
        locked_case = _case(
            self.counselor,
            locked_client,
            "DUAL-LOCKED",
            self.first,
            status=CaseStatus.CLOSED,
            submitted=True,
        )
        locked_at = locked_case.records_submitted_at
        self._register(2)
        open_case.refresh_from_db()
        locked_case.refresh_from_db()
        self.assertIsNone(open_case.records_submitted_at)
        self.assertEqual(locked_case.records_submitted_at, locked_at)
        self.http.login(email=self.counselor.email, password="pass12345")
        self.assertEqual(
            self.http.get(
                reverse("counselor:journal_create", kwargs={"pk": open_case.pk})
            ).status_code,
            200,
        )
        self.assertEqual(
            self.http.get(
                reverse("counselor:journal_create", kwargs={"pk": locked_case.pk})
            ).status_code,
            403,
        )


class EnrollmentCancellationTests(TestCase):
    def setUp(self):
        self.admin = _user("cancel-admin@example.com", "관리자", UserRole.ADMIN)
        self.counselor = _counselor("cancel-counselor@example.com", "김수련", 1)
        self.first = TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
            started_at=timezone.now() - timedelta(days=20),
        )
        self.http = Client()

    def _register(self, cohort):
        self.http.login(email=self.admin.email, password="pass12345")
        return self.http.post(
            reverse(
                "admin_panel:counselor_cohort_reenrollment",
                kwargs={"counselor_id": self.counselor.pk},
            ),
            {"cohort": str(cohort)},
            follow=True,
        )

    def _second(self):
        self._register(2)
        return TrainingEnrollment.objects.get(counselor=self.counselor, cohort=2)

    def _cancel(self, enrollment, *, follow=True):
        self.http.login(email=self.admin.email, password="pass12345")
        return self.http.post(
            reverse(
                "admin_panel:enrollment_cancel",
                kwargs={"enrollment_id": enrollment.pk},
            ),
            follow=follow,
        )

    def test_detail_shows_cancel_distinct_from_complete(self):
        second = self._second()
        self.http.login(email=self.admin.email, password="pass12345")
        page = self.http.get(
            reverse(
                "admin_panel:enrollment_training_progress_detail",
                kwargs={"enrollment_id": second.pk},
            )
        )
        self.assertContains(page, "등록 취소")
        self.assertContains(page, "이 기수 종료")
        self.assertContains(page, "김수련 상담사의 2기 등록을 취소합니다")
        self.assertContains(
            page,
            "등록 취소는 활동 이력이 없는 진행 중 추가 기수만 삭제합니다.",
        )
        first_page = self.http.get(
            reverse(
                "admin_panel:enrollment_training_progress_detail",
                kwargs={"enrollment_id": self.first.pk},
            )
        )
        self.assertContains(first_page, "이 기수 종료")
        self.assertNotContains(first_page, ">등록 취소</button>")
        self.assertContains(first_page, MSG_CANCEL_ORIGINAL)

    def test_cancel_empty_second_keeps_first_and_allows_reregistration(self):
        client = _client("cancel-client@example.com", "이내담")
        case = _case(
            self.counselor,
            client,
            "CANCEL-KEEP",
            self.first,
            status=CaseStatus.ACTIVE,
            submitted=False,
        )
        journal = CounselingJournal.objects.create(
            case=case,
            counselor=self.counselor,
            session_number=1,
            is_draft=False,
            subjective="1기기록",
        )
        appointment = _appointment(
            self.counselor,
            client,
            case,
            AppointmentStatus.CONFIRMED,
            timezone.now() + timedelta(days=2),
        )
        session = SupervisionSession.objects.create(
            cohort=1,
            session_number=1,
            scheduled_at=timezone.now() - timedelta(days=1),
            duration_minutes=50,
            counseling_method=CounselingMethod.IN_PERSON,
            status=SupervisionSessionStatus.COMPLETED,
        )
        attendance = SupervisionAttendance.objects.create(
            session=session,
            counselor=self.counselor.counselor_profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )
        submitted_at = case.records_submitted_at
        second = self._second()
        second_id = second.pk
        response = self._cancel(second)
        self.assertContains(response, "2기 등록을 취소했습니다.")
        self.assertFalse(TrainingEnrollment.objects.filter(pk=second_id).exists())
        self.first.refresh_from_db()
        case.refresh_from_db()
        journal.refresh_from_db()
        appointment.refresh_from_db()
        attendance.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(self.first.active_slot, 1)
        self.assertEqual(case.training_enrollment_id, self.first.pk)
        self.assertEqual(case.status, CaseStatus.ACTIVE)
        self.assertEqual(case.records_submitted_at, submitted_at)
        self.assertEqual(journal.subjective, "1기기록")
        self.assertEqual(appointment.status, AppointmentStatus.CONFIRMED)
        self.assertEqual(attendance.status, SupervisionAttendanceStatus.PRESENT)
        self.assertEqual(
            CounselorProfile.objects.get(user_id=self.counselor.pk).cohort,
            1,
        )
        entry = LogEntry.objects.get(object_id=str(second_id))
        self.assertNotIn("김수련", entry.object_repr)
        self.assertNotIn("김수련", entry.change_message)
        self.assertNotIn(self.counselor.email, entry.change_message)
        self.assertIn(str(self.counselor.pk), entry.change_message)
        self.assertIn('"cohort": 2', entry.change_message)
        again = self._register(2)
        self.assertContains(again, "2기 수련 등록이 완료되었습니다.")
        registered = TrainingEnrollment.objects.get(counselor=self.counselor, cohort=2)
        self.assertNotEqual(registered.pk, second_id)
        self.assertEqual(registered.status, TrainingEnrollmentStatus.ACTIVE)
        self.first.refresh_from_db()
        case.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(case.training_enrollment_id, self.first.pk)
        self.assertEqual(journal.subjective, "1기기록")

    def test_second_cancel_of_same_enrollment_is_safe(self):
        second = self._second()
        cancel_training_enrollment(second, actor=self.admin)
        with self.assertRaises(CohortReenrollmentError) as caught:
            cancel_training_enrollment(second, actor=self.admin)
        self.assertIn(MSG_CANCEL_GONE, caught.exception.messages)
        self.assertEqual(
            TrainingEnrollment.objects.get(pk=self.first.pk).status,
            TrainingEnrollmentStatus.ACTIVE,
        )
        missing = self._cancel(second, follow=False)
        self.assertEqual(missing.status_code, 404)

    def test_case_on_second_cohort_blocks_cancel(self):
        second = self._second()
        client = _client("cancel-case@example.com", "사례내담")
        case = _case(
            self.counselor,
            client,
            "CANCEL-CASE",
            second,
            status=CaseStatus.ACTIVE,
            submitted=False,
        )
        CounselingJournal.objects.create(
            case=case,
            counselor=self.counselor,
            session_number=1,
            is_draft=True,
            subjective="2기일지",
        )
        _appointment(
            self.counselor,
            client,
            case,
            AppointmentStatus.SCHEDULED,
            timezone.now() + timedelta(days=3),
        )
        TerminationCounselingRecord.objects.create(
            case=case,
            counselor=self.counselor,
            is_draft=True,
        )
        response = self._cancel(second)
        self.assertContains(response, "상담사례 1건")
        self.assertContains(response, "예약 1건")
        self.assertContains(response, "상담일지 1건")
        self.assertContains(response, "종결기록지 1건")
        self.assertTrue(TrainingEnrollment.objects.filter(pk=second.pk).exists())
        case.refresh_from_db()
        self.assertEqual(case.training_enrollment_id, second.pk)
        self.assertEqual(case.journals.get().subjective, "2기일지")
        self.assertEqual(
            TrainingEnrollment.objects.get(pk=self.first.pk).status,
            TrainingEnrollmentStatus.ACTIVE,
        )

    def test_supervision_attendance_on_second_cohort_blocks_cancel(self):
        second = self._second()
        session = SupervisionSession.objects.create(
            cohort=2,
            session_number=1,
            scheduled_at=timezone.now() - timedelta(days=1),
            duration_minutes=50,
            counseling_method=CounselingMethod.REMOTE,
            status=SupervisionSessionStatus.COMPLETED,
        )
        attendance = SupervisionAttendance.objects.create(
            session=session,
            counselor=self.counselor.counselor_profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )
        response = self._cancel(second)
        self.assertContains(response, "수퍼비전 출석 1건")
        self.assertTrue(TrainingEnrollment.objects.filter(pk=second.pk).exists())
        attendance.refresh_from_db()
        self.assertEqual(attendance.status, SupervisionAttendanceStatus.PRESENT)
        self.assertEqual(
            TrainingEnrollment.objects.get(pk=self.first.pk).status,
            TrainingEnrollmentStatus.ACTIVE,
        )

    def test_unchecked_attendance_does_not_block_cancel(self):
        second = self._second()
        session = SupervisionSession.objects.create(
            cohort=2,
            session_number=1,
            scheduled_at=timezone.now() + timedelta(days=1),
            duration_minutes=50,
            counseling_method=CounselingMethod.REMOTE,
            status=SupervisionSessionStatus.SCHEDULED,
        )
        SupervisionAttendance.objects.create(
            session=session,
            counselor=self.counselor.counselor_profile,
            status=SupervisionAttendanceStatus.UNCHECKED,
        )
        self._cancel(second)
        self.assertFalse(TrainingEnrollment.objects.filter(pk=second.pk).exists())
        self.assertTrue(SupervisionSession.objects.filter(pk=session.pk).exists())
        self.assertEqual(
            TrainingEnrollment.objects.get(pk=self.first.pk).status,
            TrainingEnrollmentStatus.ACTIVE,
        )

    def test_presentation_and_certificate_block_cancel(self):
        second = self._second()
        CasePresentationPost.objects.create(
            cohort=2,
            author=self.counselor,
            title="2기발표",
            file=SimpleUploadedFile("two.pdf", b"%PDF-1.4"),
        )
        response = self._cancel(second)
        self.assertContains(response, "사례발표 1건")
        self.assertTrue(TrainingEnrollment.objects.filter(pk=second.pk).exists())
        TrainingCertificateApproval.objects.create(
            counselor=self.counselor,
            training_enrollment=self.first,
            content_hash="a" * 64,
        )
        first_page = self.http.get(
            reverse(
                "admin_panel:enrollment_training_progress_detail",
                kwargs={"enrollment_id": self.first.pk},
            )
        )
        self.assertContains(first_page, "수련확인증 승인 1건")
        self.assertTrue(TrainingEnrollment.objects.filter(pk=self.first.pk).exists())

    def test_counselor_cannot_cancel(self):
        second = self._second()
        self.http.login(email=self.counselor.email, password="pass12345")
        response = self.http.post(
            reverse(
                "admin_panel:enrollment_cancel",
                kwargs={"enrollment_id": second.pk},
            )
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(TrainingEnrollment.objects.filter(pk=second.pk).exists())

    def test_original_enrollment_cannot_be_cancelled(self):
        second = self._second()
        response = self._cancel(self.first)
        self.assertContains(response, MSG_CANCEL_ORIGINAL)
        self.first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(second.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(second.active_slot, 2)

    def test_completed_additional_enrollment_cannot_be_cancelled(self):
        second = self._second()
        complete_training_enrollment(second)
        self.http.login(email=self.admin.email, password="pass12345")
        page = self.http.get(
            reverse(
                "admin_panel:enrollment_training_progress_detail",
                kwargs={"enrollment_id": second.pk},
            )
        )
        self.assertNotContains(page, ">등록 취소</button>")
        self.assertContains(page, MSG_CANCEL_NOT_ACTIVE)
        response = self._cancel(second)
        self.assertContains(response, MSG_CANCEL_NOT_ACTIVE)
        second.refresh_from_db()
        self.first.refresh_from_db()
        self.assertEqual(second.status, TrainingEnrollmentStatus.COMPLETED)
        self.assertIsNone(second.active_slot)
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.ACTIVE)

    def test_activity_created_during_validation_blocks_delete(self):
        second = self._second()
        client = _client("cancel-race-case@example.com", "검증내담")
        created = {}

        def add_activity(enrollment):
            created["case"] = _case(
                self.counselor,
                client,
                "CANCEL-RACE",
                enrollment,
                status=CaseStatus.ACTIVE,
                submitted=False,
            )
            session = SupervisionSession.objects.create(
                cohort=enrollment.cohort,
                session_number=1,
                scheduled_at=timezone.now() - timedelta(hours=1),
                duration_minutes=50,
                counseling_method=CounselingMethod.IN_PERSON,
                status=SupervisionSessionStatus.COMPLETED,
            )
            created["attendance"] = SupervisionAttendance.objects.create(
                session=session,
                counselor=self.counselor.counselor_profile,
                status=SupervisionAttendanceStatus.PRESENT,
            )
            created["post"] = CasePresentationPost.objects.create(
                cohort=enrollment.cohort,
                author=self.counselor,
                title="검증발표",
                file=SimpleUploadedFile("race.pdf", b"%PDF-1.4"),
            )
            return enrollment

        with patch(
            "apps.counseling.cohort_reenrollment._wait_for_cancel_race",
            add_activity,
        ):
            with self.assertRaises(CohortReenrollmentError) as caught:
                cancel_training_enrollment(second, actor=self.admin)
        self.assertIn("상담사례", str(caught.exception))
        self.assertIn("수퍼비전 출석", str(caught.exception))
        self.assertIn("사례발표", str(caught.exception))
        self.assertTrue(TrainingEnrollment.objects.filter(pk=second.pk).exists())
        self.assertFalse(
            LogEntry.objects.filter(object_id=str(second.pk)).exists()
        )
        self.assertEqual(
            TrainingEnrollment.objects.get(pk=self.first.pk).status,
            TrainingEnrollmentStatus.ACTIVE,
        )


class EnrollmentCancelConcurrencyTests(TransactionTestCase):
    def test_concurrent_cancel_deletes_the_enrollment_once(self):
        admin = _user("cancel-race-admin@example.com", "관리자", UserRole.ADMIN)
        counselor = _counselor("cancel-race@example.com", "동시수련", 1)
        first = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
            started_at=timezone.now(),
        )
        second = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=2,
            status=TrainingEnrollmentStatus.ACTIVE,
            started_at=timezone.now(),
        )
        barrier = threading.Barrier(2)
        results = []

        def worker():
            try:
                barrier.wait(timeout=10)
                cancel_training_enrollment(second, actor=admin)
                results.append("ok")
            except CohortReenrollmentError as exc:
                results.append(" ".join(exc.messages))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(TrainingEnrollment.objects.filter(pk=second.pk).exists())
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(
            all(
                item == "ok" or MSG_CANCEL_GONE in item or "겹쳤" in item
                for item in results
            )
        )
        self.assertEqual(
            TrainingEnrollment.objects.get(pk=first.pk).status,
            TrainingEnrollmentStatus.ACTIVE,
        )
        self.assertEqual(
            TrainingEnrollment.objects.filter(counselor=counselor).count(),
            1,
        )

    def test_activity_committed_during_validation_is_preserved(self):
        admin = _user("cancel-live-admin@example.com", "관리자", UserRole.ADMIN)
        counselor = _counselor("cancel-live@example.com", "검증수련", 1)
        first = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
            started_at=timezone.now(),
        )
        second = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=2,
            status=TrainingEnrollmentStatus.ACTIVE,
            started_at=timezone.now(),
        )
        client = _client("cancel-live-client@example.com", "검증내담")
        arrived = threading.Event()
        release = threading.Event()
        results = []

        def hook(enrollment):
            arrived.set()
            if not release.wait(timeout=20):
                raise TimeoutError("취소 검증 대기 시간이 지났습니다.")
            return enrollment

        def worker():
            try:
                with patch(
                    "apps.counseling.cohort_reenrollment._wait_for_cancel_race",
                    hook,
                ):
                    cancel_training_enrollment(second, actor=admin)
                results.append("ok")
            except CohortReenrollmentError as exc:
                results.append(str(exc))
            finally:
                connection.close()

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(arrived.wait(timeout=20))
        case = _case(
            counselor,
            client,
            "CANCEL-LIVE",
            second,
            status=CaseStatus.ACTIVE,
            submitted=False,
        )
        journal = CounselingJournal.objects.create(
            case=case,
            counselor=counselor,
            session_number=1,
            is_draft=False,
            subjective="검증일지",
        )
        session = SupervisionSession.objects.create(
            cohort=2,
            session_number=1,
            scheduled_at=timezone.now() - timedelta(hours=2),
            duration_minutes=50,
            counseling_method=CounselingMethod.IN_PERSON,
            status=SupervisionSessionStatus.COMPLETED,
        )
        attendance = SupervisionAttendance.objects.create(
            session=session,
            counselor=counselor.counselor_profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )
        post = CasePresentationPost.objects.create(
            cohort=2,
            author=counselor,
            title="검증발표",
            file=SimpleUploadedFile("live.pdf", b"%PDF-1.4"),
        )
        first_journal = CounselingJournal.objects.create(
            case=_case(
                counselor,
                _client("cancel-live-first@example.com", "기존내담"),
                "CANCEL-LIVE-1",
                first,
                status=CaseStatus.ACTIVE,
                submitted=False,
            ),
            counselor=counselor,
            session_number=1,
            is_draft=False,
            subjective="1기유지",
        )
        release.set()
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        self.assertTrue(results)
        self.assertNotIn("ok", results)
        self.assertIn("상담사례", results[0])
        self.assertTrue(TrainingEnrollment.objects.filter(pk=second.pk).exists())
        case.refresh_from_db()
        journal.refresh_from_db()
        attendance.refresh_from_db()
        self.assertEqual(case.training_enrollment_id, second.pk)
        self.assertEqual(journal.subjective, "검증일지")
        self.assertEqual(attendance.status, SupervisionAttendanceStatus.PRESENT)
        self.assertTrue(CasePresentationPost.objects.filter(pk=post.pk).exists())
        self.assertEqual(
            TrainingEnrollment.objects.get(pk=first.pk).status,
            TrainingEnrollmentStatus.ACTIVE,
        )
        self.assertEqual(first_journal.subjective, "1기유지")
