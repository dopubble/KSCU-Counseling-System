"""관리자·수퍼바이저 수련현황은 TrainingEnrollment 한 줄씩 집계한다."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import SupervisorProfile, User, UserRole, UserStatus
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


def _counselor(name, profile_cohort):
    user = _user(f"{name}@enroll-progress.example.com", name, UserRole.COUNSELOR)
    profile = user.counselor_profile
    profile.cohort = profile_cohort
    profile.is_approved = True
    profile.save(update_fields=["cohort", "is_approved", "updated_at"])
    return user


def _enrollment(counselor, cohort, status):
    return TrainingEnrollment.objects.create(
        counselor=counselor,
        cohort=cohort,
        status=status,
    )


def _case(counselor, number, *, enrollment, sessions):
    client = _user(f"{number}@enroll-client.example.com", number, UserRole.CLIENT)
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


def _attend(profile, cohort, number, minutes):
    session = SupervisionSession.objects.create(
        cohort=cohort,
        session_number=number,
        scheduled_at=timezone.now(),
        duration_minutes=minutes,
        status=SupervisionSessionStatus.COMPLETED,
        counseling_method=CounselingMethod.REMOTE,
    )
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


class AdminSupervisorEnrollmentProgressTests(TestCase):
    def setUp(self):
        self.counselor = _counselor("홍길동", 2)
        self.first = _enrollment(
            self.counselor, 1, TrainingEnrollmentStatus.COMPLETED
        )
        self.second = _enrollment(
            self.counselor, 2, TrainingEnrollmentStatus.ACTIVE
        )
        first_a = _case(self.counselor, "ENR-1A", enrollment=self.first, sessions=4)
        first_b = _case(self.counselor, "ENR-1B", enrollment=self.first, sessions=4)
        second = _case(self.counselor, "ENR-2A", enrollment=self.second, sessions=3)
        _intake(first_a)
        _intake(first_b)
        _intake(second)
        _journals(first_a, 5)
        _journals(first_b, 3)
        _journals(second, 3)
        _termination(first_a)
        _termination(first_b)
        _attend(self.counselor.counselor_profile, 1, 1, 90)
        _attend(self.counselor.counselor_profile, 2, 1, 50)
        first_post = _post(self.counselor, "1기보고서", cohort=1, case=first_a)
        _post(self.counselor, "1기레거시", cohort=1, case=None)
        second_post = _post(self.counselor, "2기보고서", cohort=2, case=second)
        CasePresentationComment.objects.create(
            post=first_post, author=self.counselor, content="1기"
        )
        CasePresentationComment.objects.create(
            post=second_post, author=self.counselor, content="2기"
        )
        self.admin = _user("admin-enr@example.com", "관리자", UserRole.ADMIN)
        self.supervisor_one = _user("sv1-enr@example.com", "1기수퍼", UserRole.SUPERVISOR)
        self.supervisor_two = _user("sv2-enr@example.com", "2기수퍼", UserRole.SUPERVISOR)
        SupervisorProfile.objects.update_or_create(
            user=self.supervisor_one, defaults={"assigned_cohorts": [1]}
        )
        SupervisorProfile.objects.update_or_create(
            user=self.supervisor_two, defaults={"assigned_cohorts": [2]}
        )
        self.admin_http = Client()
        self.admin_http.force_login(self.admin)
        self.admin_list = reverse("admin_panel:training_progress")

    def _row(self, response):
        return next(
            row
            for row in response.context["rows"]
            if row["counselor"].pk == self.counselor.pk
        )

    def test_admin_lists_each_enrollment_with_its_own_numbers(self):
        cohort_one = self.admin_http.get(self.admin_list, {"cohort": "1"})
        cohort_two = self.admin_http.get(self.admin_list, {"cohort": "2"})
        first = self._row(cohort_one)
        second = self._row(cohort_two)
        self.assertEqual(first["enrollment"], self.first)
        self.assertEqual(second["enrollment"], self.second)
        self.assertEqual(first["progress"]["intake_completed_count"], 2)
        self.assertEqual(first["progress"]["journal_completed_count"], 8)
        self.assertEqual(first["progress"]["termination_completed_count"], 2)
        self.assertEqual(first["progress"]["supervision_attended_minutes"], 90)
        self.assertEqual(first["progress"]["case_report_submitted_count"], 2)
        self.assertEqual(first["progress"]["training_completed_minutes"], 690)
        self.assertEqual(second["progress"]["intake_completed_count"], 1)
        self.assertEqual(second["progress"]["journal_completed_count"], 3)
        self.assertEqual(second["progress"]["termination_completed_count"], 0)
        self.assertEqual(second["progress"]["supervision_attended_minutes"], 50)
        self.assertEqual(second["progress"]["case_report_submitted_count"], 1)
        self.assertEqual(second["progress"]["training_completed_minutes"], 290)
        self.assertEqual(cohort_one.context["selected_cohort"], 1)
        self.assertIn(2, cohort_one.context["cohorts"])
        self.assertContains(cohort_one, ">1</td>")

    def test_profile_cohort_does_not_remove_the_other_enrollment(self):
        self.assertEqual(self.counselor.counselor_profile.cohort, 2)
        response = self.admin_http.get(self.admin_list, {"cohort": "1"})
        self.assertEqual(self._row(response)["enrollment"], self.first)

    def test_enrollment_detail_urls_stay_separate(self):
        first_url = reverse(
            "admin_panel:enrollment_training_progress_detail",
            kwargs={"enrollment_id": self.first.pk},
        )
        second_url = reverse(
            "admin_panel:enrollment_training_progress_detail",
            kwargs={"enrollment_id": self.second.pk},
        )
        first = self.admin_http.get(first_url)
        second = self.admin_http.get(second_url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.context["training_progress"]["journal_completed_count"], 8)
        self.assertEqual(second.context["training_progress"]["journal_completed_count"], 3)
        self.assertContains(first, "수련 기수:")
        self.assertContains(first, "1기")
        self.assertNotContains(first, "1기 (현재)")
        self.assertContains(second, "2기 (현재)")
        self.assertNotContains(first, "수료")
        self.assertContains(first, "수련확인증")

    def test_legacy_counselor_url_redirects_to_the_single_active_enrollment(self):
        response = self.admin_http.get(
            reverse(
                "admin_panel:counselor_training_progress_detail",
                kwargs={"counselor_id": self.counselor.pk},
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(str(self.second.pk), response.url)
        followed = self.admin_http.get(response.url)
        self.assertEqual(followed.context["training_progress"]["journal_completed_count"], 3)

    def test_supervisor_can_open_only_the_assigned_enrollment(self):
        first_url = reverse(
            "supervisor:enrollment_training_progress_detail",
            kwargs={"enrollment_id": self.first.pk},
        )
        second_url = reverse(
            "supervisor:enrollment_training_progress_detail",
            kwargs={"enrollment_id": self.second.pk},
        )
        one = Client()
        two = Client()
        one.force_login(self.supervisor_one)
        two.force_login(self.supervisor_two)
        self.assertEqual(one.get(first_url).status_code, 200)
        self.assertEqual(one.get(second_url).status_code, 403)
        self.assertEqual(two.get(second_url).status_code, 200)
        self.assertEqual(two.get(first_url).status_code, 403)
        listing = one.get(reverse("supervisor:training_progress"), {"cohort": "1"})
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(self._row(listing)["progress"]["journal_completed_count"], 8)
        self.assertEqual(
            two.get(reverse("supervisor:training_progress"), {"cohort": "1"}).status_code,
            403,
        )

    def test_legacy_supervisor_url_uses_enrollment_cohort_not_profile(self):
        one = Client()
        one.force_login(self.supervisor_one)
        response = one.get(
            reverse(
                "supervisor:counselor_training_progress_detail",
                kwargs={"counselor_id": self.counselor.pk},
            )
        )
        self.assertEqual(response.status_code, 403)

    def test_counselor_without_enrollment_is_omitted(self):
        _counselor("무기수", None)
        response = self.admin_http.get(self.admin_list, {"cohort": "2"})
        names = [row["counselor"].name for row in response.context["rows"]]
        self.assertNotIn("무기수", names)

    def test_ambiguous_legacy_url_does_not_show_combined_progress(self):
        counselor = _counselor("불명확", 3)
        _enrollment(counselor, 3, TrainingEnrollmentStatus.COMPLETED)
        _enrollment(counselor, 4, TrainingEnrollmentStatus.COMPLETED)
        _case(counselor, "AMB-3", enrollment=TrainingEnrollment.objects.get(counselor=counselor, cohort=3), sessions=9)
        response = self.admin_http.get(
            reverse(
                "admin_panel:counselor_training_progress_detail",
                kwargs={"counselor_id": counselor.pk},
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_single_enrollment_legacy_url_redirects_to_that_enrollment(self):
        counselor = _counselor("단일", 8)
        enrollment = _enrollment(counselor, 8, TrainingEnrollmentStatus.ACTIVE)
        case = _case(counselor, "ONE-8", enrollment=enrollment, sessions=2)
        _journals(case, 1)
        response = self.admin_http.get(
            reverse(
                "admin_panel:counselor_training_progress_detail",
                kwargs={"counselor_id": counselor.pk},
            ),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["training_enrollment"], enrollment)
        self.assertEqual(response.context["training_progress"]["journal_completed_count"], 1)
        listing = self.admin_http.get(
            reverse("admin_panel:training_progress"), {"cohort": "8"}
        )
        self.assertEqual(len(listing.context["rows"]), 1)
        self.assertEqual(listing.context["rows"][0]["progress"]["journal_completed_count"], 1)
