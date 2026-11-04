"""수퍼바이저 상담기록은 사례 Enrollment 기수로 나눈다."""

from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import SupervisorProfile, User, UserRole, UserStatus
from apps.counseling.journal_permissions import (
    user_can_browse_counselor_journals,
    user_can_download_initial_counseling_record_pdf,
    user_can_download_journal_pdf,
    user_can_view_initial_counseling_record,
    user_can_view_journal,
)
from apps.counseling.models import TrainingEnrollment, TrainingEnrollmentStatus
from apps.counseling.tests.test_journal_permissions import (
    _create_case,
    _create_client,
    _create_counselor,
    _create_initial_record,
    _create_journal,
)
from apps.counseling.views import user_can_edit_journal
from apps.sessions_app.models import CounselingJournal, TerminationCounselingRecord


def _supervisor(email, name, cohorts):
    user = User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=UserRole.SUPERVISOR,
        status=UserStatus.ACTIVE,
    )
    SupervisorProfile.objects.update_or_create(
        user=user,
        defaults={"assigned_cohorts": cohorts},
    )
    return user


class SupervisorEnrollmentRecordTests(TestCase):
    def setUp(self):
        self.counselor = _create_counselor("홍길동", cohort=2)
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
        self.client_one = _create_client("일길동")
        self.client_two = _create_client("이길동")
        self.client_orphan = _create_client("고아남")
        self.case_one = _create_case(self.counselor, self.client_one, "CASE-E2-1")
        self.case_two = _create_case(self.counselor, self.client_two, "CASE-E2-2")
        self.case_orphan = _create_case(self.counselor, self.client_orphan, "CASE-E2-ORPHAN")
        self.case_one.training_enrollment = self.first
        self.case_two.training_enrollment = self.second
        self.case_one.save(update_fields=["training_enrollment"])
        self.case_two.save(update_fields=["training_enrollment"])
        self.journal_one = _create_journal(self.case_one, self.counselor, session_number=1)
        self.journal_one.subjective = "일기기밀문장"
        self.journal_one.save(update_fields=["subjective", "updated_at"])
        self.journal_two = _create_journal(self.case_two, self.counselor, session_number=1)
        self.journal_two.subjective = "이기기밀문장"
        self.journal_two.save(update_fields=["subjective", "updated_at"])
        self.journal_orphan = _create_journal(self.case_orphan, self.counselor, session_number=1)
        self.draft = CounselingJournal.objects.create(
            case=self.case_one,
            counselor=self.counselor,
            session_number=9,
            is_draft=True,
            subjective="초안비밀문장",
        )
        self.record_one = _create_initial_record(self.case_one, self.counselor)
        self.record_one.presented_problems_summary = "일기초호소비밀"
        self.record_one.save(update_fields=["presented_problems_summary", "updated_at"])
        self.record_two = _create_initial_record(self.case_two, self.counselor)
        self.record_two.presented_problems_summary = "이기초호소비밀"
        self.record_two.save(update_fields=["presented_problems_summary", "updated_at"])
        self.record_orphan = _create_initial_record(self.case_orphan, self.counselor)
        self.record_orphan.presented_problems_summary = "고아호소비밀"
        self.record_orphan.save(update_fields=["presented_problems_summary", "updated_at"])
        self.termination_one = TerminationCounselingRecord.objects.create(
            case=self.case_one,
            counselor=self.counselor,
            is_draft=False,
            counselor_opinion="일종결비밀",
        )
        self.termination_two = TerminationCounselingRecord.objects.create(
            case=self.case_two,
            counselor=self.counselor,
            is_draft=False,
            counselor_opinion="이종결비밀",
        )
        self.legacy = _create_counselor("단기수상담", cohort=1)
        self.legacy_case = _create_case(self.legacy, _create_client("단길동"), "CASE-E2-LEGACY")
        self.legacy_journal = _create_journal(self.legacy_case, self.legacy, session_number=1)
        self.legacy_record = _create_initial_record(self.legacy_case, self.legacy)
        self.supervisor_x = _supervisor("sv-e2-x@example.com", "김교수", [1])
        self.supervisor_y = _supervisor("sv-e2-y@example.com", "이교수", [2])
        self.supervisor_z = _supervisor("sv-e2-z@example.com", "박교수", [1, 2])
        self.admin = User.objects.create_user(
            email="admin-e2@example.com",
            password="pass12345",
            name="관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        self.list_url = reverse("supervisor:cohort_journals")
        self.detail_url = reverse(
            "supervisor:counselor_journals",
            kwargs={"counselor_id": self.counselor.pk},
        )
        self.initial_list_url = reverse("supervisor:cohort_initial_records")

    def _login(self, user):
        http = HttpClient()
        http.force_login(user)
        return http

    def test_profile_browse_permission_stays_on_profile_cohort(self):
        self.assertFalse(user_can_browse_counselor_journals(self.supervisor_x, self.counselor))
        self.assertTrue(user_can_browse_counselor_journals(self.supervisor_y, self.counselor))

    def test_assigned_supervisor_sees_only_that_enrollment_cohort(self):
        first = self._login(self.supervisor_x).get(self.list_url)
        self.assertEqual(first.status_code, 200)
        self.assertContains(first, "홍길동")
        self.assertContains(first, "단기수상담")
        self.assertContains(first, "1기")
        self.assertNotContains(first, "2기")
        self.assertNotContains(first, "상담일지 2건")
        self.assertContains(first, f"{self.detail_url}?cohort=1")

        second = self._login(self.supervisor_y).get(self.list_url)
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, "홍길동")
        self.assertContains(second, "2기")
        self.assertNotContains(second, "단기수상담")
        self.assertNotContains(second, "1기")
        self.assertContains(second, f"{self.detail_url}?cohort=2")

    def test_multi_assigned_supervisor_sections_do_not_mix_cases(self):
        response = self._login(self.supervisor_z).get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"{self.detail_url}?cohort=1")
        self.assertContains(response, f"{self.detail_url}?cohort=2")
        self.assertNotContains(response, "상담일지 2건")

        cohort_one = self._login(self.supervisor_z).get(f"{self.detail_url}?cohort=1")
        self.assertEqual(cohort_one.status_code, 200)
        self.assertContains(cohort_one, "[1기] 홍길동 상담일지")
        self.assertContains(cohort_one, "일*동")
        self.assertContains(cohort_one, "사례 1건 | 완료 상담 1회기")
        self.assertNotContains(cohort_one, "이*동")
        self.assertNotContains(cohort_one, "고*남")
        self.assertNotContains(cohort_one, "9회기")
        self.assertNotContains(cohort_one, "일기기밀문장")
        self.assertNotContains(cohort_one, "이기기밀문장")
        self.assertNotContains(cohort_one, "일종결비밀")

        cohort_two = self._login(self.supervisor_z).get(f"{self.detail_url}?cohort=2")
        self.assertEqual(cohort_two.status_code, 200)
        self.assertContains(cohort_two, "[2기] 홍길동 상담일지")
        self.assertContains(cohort_two, "이*동")
        self.assertNotContains(cohort_two, "일*동")
        self.assertNotContains(cohort_two, "고*남")

        mixed = self._login(self.supervisor_z).get(self.detail_url)
        self.assertEqual(mixed.status_code, 403)

    def test_direct_journal_urls_follow_case_cohort(self):
        self.assertTrue(user_can_view_journal(self.supervisor_x, self.journal_one))
        self.assertTrue(user_can_download_journal_pdf(self.supervisor_x, self.journal_one))
        self.assertFalse(user_can_view_journal(self.supervisor_x, self.journal_two))
        self.assertFalse(user_can_view_journal(self.supervisor_x, self.journal_orphan))
        self.assertFalse(user_can_view_journal(self.supervisor_x, self.draft))
        self.assertTrue(user_can_view_journal(self.supervisor_y, self.journal_two))
        self.assertFalse(user_can_view_journal(self.supervisor_y, self.journal_one))
        self.assertFalse(user_can_view_journal(self.supervisor_y, self.journal_orphan))
        self.assertTrue(user_can_view_journal(self.supervisor_x, self.legacy_journal))
        self.assertFalse(user_can_view_journal(self.supervisor_y, self.legacy_journal))

        x_http = self._login(self.supervisor_x)
        own = x_http.get(self.detail_url)
        self.assertEqual(own.status_code, 200)
        self.assertContains(own, "일*동")
        self.assertContains(own, "작성완료")
        self.assertNotContains(own, "이*동")
        denied = x_http.get(f"{self.detail_url}?cohort=2")
        self.assertEqual(denied.status_code, 403)
        pdf = x_http.post(
            reverse("supervisor:journal_pdf", kwargs={"journal_pk": self.journal_two.pk}),
            {"pdf_password": "test1234"},
        )
        self.assertEqual(pdf.status_code, 403)
        self.assertNotContains(pdf, "이기기밀문장", status_code=403)

        y_http = self._login(self.supervisor_y)
        self.assertEqual(y_http.get(f"{self.detail_url}?cohort=1").status_code, 403)
        self.assertEqual(
            y_http.post(
                reverse("supervisor:journal_pdf", kwargs={"journal_pk": self.journal_one.pk}),
                {"pdf_password": "test1234"},
            ).status_code,
            403,
        )
        counselor_pdf = y_http.post(
            reverse(
                "counselor:journal_pdf",
                kwargs={"pk": self.case_two.pk, "session_number": 1},
            ),
            {"pdf_password": "test1234"},
        )
        self.assertEqual(counselor_pdf.status_code, 403)

    def test_initial_records_follow_case_cohort(self):
        self.assertTrue(user_can_view_initial_counseling_record(self.supervisor_x, self.record_one))
        self.assertTrue(
            user_can_download_initial_counseling_record_pdf(self.supervisor_x, self.record_one)
        )
        self.assertFalse(user_can_view_initial_counseling_record(self.supervisor_x, self.record_two))
        self.assertFalse(
            user_can_view_initial_counseling_record(self.supervisor_x, self.record_orphan)
        )
        self.assertTrue(user_can_view_initial_counseling_record(self.supervisor_y, self.record_two))
        self.assertFalse(user_can_view_initial_counseling_record(self.supervisor_y, self.record_one))
        self.assertTrue(
            user_can_view_initial_counseling_record(self.supervisor_x, self.legacy_record)
        )
        self.assertFalse(
            user_can_view_initial_counseling_record(self.supervisor_y, self.legacy_record)
        )

        x_http = self._login(self.supervisor_x)
        listed = x_http.get(self.initial_list_url)
        self.assertContains(listed, "CASE-E2-1")
        self.assertContains(listed, "CASE-E2-LEGACY")
        self.assertNotContains(listed, "CASE-E2-2")
        self.assertNotContains(listed, "CASE-E2-ORPHAN")
        self.assertNotContains(listed, "이기초호소비밀")
        self.assertNotContains(listed, "고아호소비밀")
        detail = x_http.get(
            reverse("supervisor:initial_record_detail", kwargs={"record_pk": self.record_one.pk})
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "일기초호소비밀")
        denied = x_http.get(
            reverse("supervisor:initial_record_detail", kwargs={"record_pk": self.record_two.pk})
        )
        self.assertEqual(denied.status_code, 403)
        self.assertNotContains(denied, "이기초호소비밀", status_code=403)
        self.assertEqual(
            x_http.post(
                reverse(
                    "supervisor:initial_record_pdf",
                    kwargs={"record_pk": self.record_two.pk},
                ),
                {"pdf_password": "test1234"},
            ).status_code,
            403,
        )

        y_listed = self._login(self.supervisor_y).get(self.initial_list_url)
        self.assertContains(y_listed, "CASE-E2-2")
        self.assertNotContains(y_listed, "CASE-E2-1")
        self.assertNotContains(y_listed, "CASE-E2-LEGACY")
        self.assertNotContains(y_listed, "CASE-E2-ORPHAN")

    def test_termination_badge_stays_on_the_case_cohort(self):
        page = self._login(self.supervisor_x).get(self.detail_url)
        self.assertContains(page, 'data-row="termination"')
        self.assertContains(page, "작성완료")
        self.assertNotContains(page, "이*동")
        self.assertNotContains(page, "이종결비밀")
        supervisor_url = reverse(
            "counselor:termination_record_detail",
            kwargs={"pk": self.case_two.pk},
        )
        self.assertEqual(self._login(self.supervisor_x).get(supervisor_url).status_code, 403)
        self.assertEqual(self._login(self.supervisor_y).get(supervisor_url).status_code, 403)

    def test_counselor_still_opens_past_and_current_cases(self):
        http = self._login(self.counselor)
        for case, journal in (
            (self.case_one, self.journal_one),
            (self.case_two, self.journal_two),
            (self.case_orphan, self.journal_orphan),
        ):
            detail = http.get(reverse("counselor:case_detail", kwargs={"pk": case.pk}))
            self.assertEqual(detail.status_code, 200)
            journal_page = http.get(
                reverse(
                    "counselor:journal_detail",
                    kwargs={"pk": case.pk, "session_number": journal.session_number},
                )
            )
            self.assertEqual(journal_page.status_code, 200)
            self.assertContains(journal_page, journal.subjective)
        for case in (self.case_one, self.case_two):
            termination = http.get(
                reverse("counselor:termination_record_detail", kwargs={"pk": case.pk})
            )
            self.assertEqual(termination.status_code, 200)
        self.assertTrue(user_can_view_journal(self.counselor, self.journal_one))
        self.assertTrue(user_can_view_journal(self.counselor, self.journal_two))
        self.assertTrue(user_can_edit_journal(self.counselor, self.journal_one))
        self.assertFalse(user_can_edit_journal(self.supervisor_x, self.journal_one))

    def test_submitted_journal_stays_locked_for_the_author(self):
        self.case_one.records_submitted_at = timezone.now()
        self.case_one.save(update_fields=["records_submitted_at"])
        response = self._login(self.counselor).get(
            reverse(
                "counselor:journal_edit",
                kwargs={"pk": self.case_one.pk, "session_number": 1},
            )
        )
        self.assertEqual(response.status_code, 403)
        self.journal_one.refresh_from_db()
        self.draft.refresh_from_db()
        self.assertFalse(self.journal_one.is_draft)
        self.assertTrue(self.draft.is_draft)
        self.assertFalse(user_can_edit_journal(self.supervisor_y, self.journal_two))

    def test_admin_keeps_both_cohorts_and_unscoped_records(self):
        http = self._login(self.admin)
        listed = http.get(self.list_url)
        self.assertContains(listed, "전체 기수")
        self.assertContains(listed, "홍길동")
        self.assertContains(listed, "단기수상담")
        self.assertContains(listed, f"{self.detail_url}?cohort=1")
        self.assertContains(listed, f"{self.detail_url}?cohort=2")
        everything = http.get(self.detail_url)
        self.assertEqual(everything.status_code, 200)
        self.assertContains(everything, "일*동")
        self.assertContains(everything, "이*동")
        self.assertContains(everything, "고*남")
        initials = http.get(self.initial_list_url)
        self.assertContains(initials, "CASE-E2-1")
        self.assertContains(initials, "CASE-E2-2")
        self.assertContains(initials, "CASE-E2-ORPHAN")
        self.assertContains(initials, "CASE-E2-LEGACY")
        self.assertTrue(user_can_view_journal(self.admin, self.journal_one))
        self.assertTrue(user_can_view_journal(self.admin, self.journal_two))
        self.assertTrue(user_can_view_journal(self.admin, self.journal_orphan))
        self.assertTrue(user_can_view_initial_counseling_record(self.admin, self.record_two))

    def test_case_enrollment_values_stay_unchanged(self):
        self._login(self.supervisor_z).get(f"{self.detail_url}?cohort=1")
        self._login(self.supervisor_z).get(f"{self.detail_url}?cohort=2")
        self.case_one.refresh_from_db()
        self.case_two.refresh_from_db()
        self.case_orphan.refresh_from_db()
        self.counselor.counselor_profile.refresh_from_db()
        self.assertEqual(self.case_one.training_enrollment_id, self.first.pk)
        self.assertEqual(self.case_two.training_enrollment_id, self.second.pk)
        self.assertIsNone(self.case_orphan.training_enrollment_id)
        self.assertEqual(self.counselor.counselor_profile.cohort, 2)
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.COMPLETED)
        self.assertEqual(self.second.status, TrainingEnrollmentStatus.ACTIVE)
