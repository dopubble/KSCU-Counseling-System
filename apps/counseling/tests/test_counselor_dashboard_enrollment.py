"""상담사 대시보드 수련현황은 선택한 TrainingEnrollment만 집계한다."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
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
    TrainingEnrollment,
    TrainingEnrollmentStatus,
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


def _counselor(name, cohort):
    user = _user(f"{name}@dash.example.com", name, UserRole.COUNSELOR)
    profile = user.counselor_profile
    profile.cohort = cohort
    profile.is_approved = True
    profile.save(update_fields=["cohort", "is_approved", "updated_at"])
    return user


def _case(counselor, number, *, enrollment, sessions):
    client = _user(f"{number}@dash-client.example.com", number, UserRole.CLIENT)
    application = CounselingApplication.objects.create(
        client=client,
        counseling_types=["진로상담"],
        reason="수련",
        counseling_method=CounselingMethod.IN_PERSON,
        status=ApplicationStatus.IN_PROGRESS,
    )
    return Case.objects.create(
        application=application,
        client=client,
        counselor=counselor,
        training_enrollment=enrollment,
        case_number=number,
        status=CaseStatus.ACTIVE,
        counseling_method=CounselingMethod.IN_PERSON,
        total_sessions=sessions,
        remaining_sessions=sessions,
    )


def _intake(case):
    return ConsentDocument.objects.create(
        client=case.client,
        application=case.application,
        doc_type=ConsentDocType.INTAKE,
        file="consents/intake.pdf",
        uploaded_by=case.counselor,
    )


def _journals(case, count):
    for number in range(1, count + 1):
        CounselingJournal.objects.create(
            case=case,
            counselor=case.counselor,
            session_number=number,
            is_draft=False,
            counseling_method=CounselingMethod.IN_PERSON,
            subjective="S",
            objective="O",
            assessment="A",
            plan="P",
        )


def _termination(case):
    return TerminationCounselingRecord.objects.create(
        case=case,
        counselor=case.counselor,
        is_draft=False,
    )


def _session(cohort, number, *, minutes):
    return SupervisionSession.objects.create(
        cohort=cohort,
        session_number=number,
        scheduled_at=timezone.now(),
        duration_minutes=minutes,
        status=SupervisionSessionStatus.COMPLETED,
        counseling_method=CounselingMethod.REMOTE,
    )


def _attend(profile, session):
    return SupervisionAttendance.objects.create(
        session=session,
        counselor=profile,
        status=SupervisionAttendanceStatus.PRESENT,
    )


def _post(author, title, *, cohort, case=None):
    return CasePresentationPost.objects.create(
        cohort=cohort,
        author=author,
        case=case,
        title=title,
        file=SimpleUploadedFile(f"{title}.pdf", b"%PDF-1.4"),
    )


class CounselorDashboardEnrollmentTests(TestCase):
    def setUp(self):
        self.counselor = _counselor("다기수", 2)
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
        first_a = _case(self.counselor, "DASH-1A", enrollment=self.first, sessions=4)
        first_b = _case(self.counselor, "DASH-1B", enrollment=self.first, sessions=4)
        second = _case(self.counselor, "DASH-2A", enrollment=self.second, sessions=3)
        _intake(first_a)
        _intake(first_b)
        _intake(second)
        _journals(first_a, 5)
        _journals(first_b, 3)
        _journals(second, 3)
        _termination(first_a)
        _termination(first_b)
        _attend(self.counselor.counselor_profile, _session(1, 1, minutes=90))
        _attend(self.counselor.counselor_profile, _session(2, 1, minutes=50))
        first_post = _post(self.counselor, "1기보고서", cohort=1, case=first_a)
        _post(self.counselor, "1기레거시", cohort=1, case=None)
        second_post = _post(self.counselor, "2기보고서", cohort=2, case=second)
        CasePresentationComment.objects.create(post=first_post, author=self.counselor, content="1기")
        CasePresentationComment.objects.create(post=second_post, author=self.counselor, content="2기")
        self.http = Client()
        self.http.force_login(self.counselor)
        self.url = reverse("counselor:dashboard")

    def test_default_dashboard_uses_the_active_enrollment(self):
        response = self.http.get(self.url)
        progress = response.context["training_progress"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_training_enrollment"], self.second)
        self.assertEqual(progress["intake_required_count"], 1)
        self.assertEqual(progress["intake_completed_count"], 1)
        self.assertEqual(progress["journal_completed_count"], 3)
        self.assertEqual(progress["counseling_assigned_count"], 3)
        self.assertEqual(progress["termination_completed_count"], 0)
        self.assertEqual(progress["supervision_attended_count"], 1)
        self.assertEqual(progress["supervision_attended_minutes"], 50)
        self.assertEqual(progress["case_report_submitted_count"], 1)
        self.assertEqual(progress["public_presentation_participation_count"], 1)
        self.assertEqual(progress["training_completed_minutes"], 290)
        self.assertContains(response, "2기 (현재)")
        self.assertContains(response, "1기")
        self.assertNotContains(response, "수료")

    def test_selected_completed_enrollment_ignores_profile_cohort(self):
        response = self.http.get(self.url, {"enrollment": str(self.first.pk)})
        progress = response.context["training_progress"]
        self.assertEqual(response.context["selected_training_enrollment"], self.first)
        self.assertEqual(self.counselor.counselor_profile.cohort, 2)
        self.assertEqual(progress["intake_required_count"], 2)
        self.assertEqual(progress["intake_completed_count"], 2)
        self.assertEqual(progress["journal_completed_count"], 8)
        self.assertEqual(progress["counseling_assigned_count"], 8)
        self.assertEqual(progress["termination_required_count"], 2)
        self.assertEqual(progress["termination_completed_count"], 2)
        self.assertEqual(progress["supervision_attended_minutes"], 90)
        self.assertEqual(progress["case_report_submitted_count"], 2)
        self.assertEqual(progress["public_presentation_participation_count"], 1)
        self.assertEqual(progress["training_in_person_minutes"], 600)
        self.assertEqual(progress["training_remote_minutes"], 90)
        self.assertEqual(progress["training_completed_minutes"], 690)

    def test_explicit_active_enrollment_stays_on_that_enrollment(self):
        response = self.http.get(self.url, {"enrollment": str(self.second.pk)})
        progress = response.context["training_progress"]
        self.assertEqual(progress["journal_completed_count"], 3)
        self.assertEqual(progress["training_completed_minutes"], 290)

    def test_other_counselor_enrollment_is_not_visible(self):
        other = _counselor("다른상담", 4)
        secret = TrainingEnrollment.objects.create(
            counselor=other,
            cohort=4,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        _case(other, "DASH-SECRET", enrollment=secret, sessions=9)
        response = self.http.get(self.url, {"enrollment": str(secret.pk)})
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "DASH-SECRET", status_code=404)

    def test_single_enrollment_hides_the_cohort_dropdown(self):
        counselor = _counselor("단일기수", 3)
        enrollment = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=3,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        case = _case(counselor, "DASH-ONE", enrollment=enrollment, sessions=2)
        _intake(case)
        _journals(case, 1)
        http = Client()
        http.force_login(counselor)
        response = http.get(reverse("counselor:dashboard"))
        progress = response.context["training_progress"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["training_enrollment_choices"]), 1)
        self.assertNotContains(response, 'name="enrollment"')
        self.assertEqual(progress["intake_completed_count"], 1)
        self.assertEqual(progress["journal_completed_count"], 1)
        self.assertEqual(progress["counseling_assigned_count"], 2)

    def test_counselor_without_enrollment_does_not_use_all_cases(self):
        counselor = _counselor("무기수화면", None)
        case = _case(
            counselor,
            "DASH-NONE",
            enrollment=None,
            sessions=6,
        )
        _intake(case)
        _journals(case, 4)
        http = Client()
        http.force_login(counselor)
        response = http.get(reverse("counselor:dashboard"))
        progress = response.context["training_progress"]
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "등록된 수련 기수가 없습니다.")
        self.assertIsNone(response.context["selected_training_enrollment"])
        self.assertEqual(progress["intake_required_count"], 0)
        self.assertEqual(progress["journal_completed_count"], 0)
        self.assertEqual(progress["training_completed_minutes"], 0)
        self.assertNotContains(response, "접수면접")
