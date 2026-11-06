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


def _cohort_section(body: str, label: str) -> str:
    marker = f">{label}</h2>"
    start = body.index(marker)
    rest = body[start:]
    nxt = rest.find("<h2", len(marker))
    if nxt == -1:
        return rest
    return rest[:nxt]


class EnrollmentRosterWithoutCasesTests(TestCase):
    """재등록 상담사는 사례가 없는 새 기수에도 0건으로 나온다."""

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
        self.case_a = _create_case(self.counselor, _create_client("일재담"), "CASE-ROSTER-A")
        self.case_b = _create_case(self.counselor, _create_client("이재담"), "CASE-ROSTER-B")
        self.case_a.training_enrollment = self.first
        self.case_b.training_enrollment = self.first
        self.case_a.save(update_fields=["training_enrollment"])
        self.case_b.save(update_fields=["training_enrollment"])
        self.journals = [
            _create_journal(self.case_a, self.counselor, session_number=number)
            for number in range(1, 7)
        ] + [
            _create_journal(self.case_b, self.counselor, session_number=number)
            for number in range(1, 5)
        ]
        self.only_first = _create_counselor("일만등록", cohort=1)
        self.only_first_enrollment = TrainingEnrollment.objects.create(
            counselor=self.only_first,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        self.only_first_case = _create_case(
            self.only_first, _create_client("단재담"), "CASE-ROSTER-ONLY"
        )
        self.only_first_case.training_enrollment = self.only_first_enrollment
        self.only_first_case.save(update_fields=["training_enrollment"])
        self.only_first_journal = _create_journal(
            self.only_first_case, self.only_first, session_number=1
        )
        self.legacy = _create_counselor("레거시단독", cohort=1)
        self.legacy_case = _create_case(self.legacy, _create_client("레재담"), "CASE-ROSTER-LEGACY")
        self.legacy_journal = _create_journal(self.legacy_case, self.legacy, session_number=1)
        self.supervisor_one = _supervisor("sv-roster-1@example.com", "일교수", [1])
        self.supervisor_two = _supervisor("sv-roster-2@example.com", "이교수", [2])
        self.supervisor_both = _supervisor("sv-roster-both@example.com", "박교수", [1, 2])
        self.detail_url = reverse(
            "supervisor:counselor_journals",
            kwargs={"counselor_id": self.counselor.pk},
        )

    def _login(self, user):
        http = HttpClient()
        http.force_login(user)
        return http

    def test_reenrolled_counselor_is_listed_in_both_cohorts_with_separate_counts(self):
        response = self._login(self.supervisor_both).get(reverse("supervisor:cohort_journals"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        first = _cohort_section(body, "1기")
        second = _cohort_section(body, "2기")

        self.assertIn("홍길동", first)
        self.assertIn("상담일지 10건", first)
        self.assertIn("일만등록", first)
        self.assertIn("레거시단독", first)
        self.assertIn(f"{self.detail_url}?cohort=1", first)
        self.assertIn("홍길동", second)
        self.assertIn("상담일지 0건", second)
        self.assertIn(f"{self.detail_url}?cohort=2", second)
        self.assertNotIn("일만등록", second)
        self.assertNotIn("레거시단독", second)
        self.assertNotIn("상담일지 10건", second)
        self.assertNotIn("상담일지 1건", second)

        empty = self._login(self.supervisor_two).get(f"{self.detail_url}?cohort=2")
        self.assertEqual(empty.status_code, 200)
        self.assertContains(empty, "[2기] 홍길동 상담일지")
        self.assertContains(empty, "아직 작성 완료된 상담일지가 없습니다.")
        self.assertNotContains(empty, "일*담")
        self.assertNotContains(empty, "이*담")

        cohort_two_case = _create_case(self.counselor, _create_client("삼재담"), "CASE-ROSTER-2")
        cohort_two_case.training_enrollment = self.second
        cohort_two_case.save(update_fields=["training_enrollment"])
        cohort_two_journal = _create_journal(cohort_two_case, self.counselor, session_number=1)

        listed = self._login(self.supervisor_both).get(reverse("supervisor:cohort_journals"))
        listed_body = listed.content.decode()
        self.assertIn("상담일지 10건", _cohort_section(listed_body, "1기"))
        self.assertNotIn("상담일지 11건", _cohort_section(listed_body, "1기"))
        self.assertIn("상담일지 1건", _cohort_section(listed_body, "2기"))
        self.assertNotIn("상담일지 10건", _cohort_section(listed_body, "2기"))

        cohort_one = self._login(self.supervisor_both).get(f"{self.detail_url}?cohort=1")
        self.assertContains(cohort_one, "일*담")
        self.assertContains(cohort_one, "이*담")
        self.assertContains(cohort_one, "사례 2건 | 완료 상담 10회기")
        self.assertNotContains(cohort_one, "삼*담")

        cohort_two = self._login(self.supervisor_both).get(f"{self.detail_url}?cohort=2")
        self.assertContains(cohort_two, "삼*담")
        self.assertContains(cohort_two, "사례 1건 | 완료 상담 1회기")
        self.assertNotContains(cohort_two, "일*담")
        self.assertNotContains(cohort_two, "이*담")

        self.assertEqual(
            self._login(self.supervisor_one).get(f"{self.detail_url}?cohort=2").status_code,
            403,
        )
        self.assertEqual(
            self._login(self.supervisor_two).get(f"{self.detail_url}?cohort=1").status_code,
            403,
        )
        self.assertEqual(
            self._login(self.supervisor_one).get(reverse("supervisor:cohort_journals")).status_code,
            200,
        )
        one_only = self._login(self.supervisor_one).get(reverse("supervisor:cohort_journals"))
        self.assertContains(one_only, "1기")
        self.assertContains(one_only, "홍길동")
        self.assertContains(one_only, "일만등록")
        self.assertContains(one_only, "레거시단독")
        self.assertNotContains(one_only, "2기")
        two_only = self._login(self.supervisor_two).get(reverse("supervisor:cohort_journals"))
        self.assertContains(two_only, "2기")
        self.assertContains(two_only, "홍길동")
        self.assertNotContains(two_only, "1기")
        self.assertNotContains(two_only, "일만등록")
        self.assertNotContains(two_only, "레거시단독")

        for journal in self.journals:
            self.assertTrue(user_can_view_journal(self.supervisor_one, journal))
            self.assertTrue(user_can_download_journal_pdf(self.supervisor_one, journal))
            self.assertFalse(user_can_view_journal(self.supervisor_two, journal))
            self.assertFalse(user_can_download_journal_pdf(self.supervisor_two, journal))
        self.assertTrue(user_can_view_journal(self.supervisor_two, cohort_two_journal))
        self.assertTrue(user_can_download_journal_pdf(self.supervisor_two, cohort_two_journal))
        self.assertFalse(user_can_view_journal(self.supervisor_one, cohort_two_journal))
        self.assertFalse(user_can_download_journal_pdf(self.supervisor_one, cohort_two_journal))
        self.assertTrue(user_can_view_journal(self.supervisor_one, self.legacy_journal))
        self.assertFalse(user_can_view_journal(self.supervisor_two, self.legacy_journal))
        self.assertTrue(user_can_view_journal(self.supervisor_one, self.only_first_journal))
        self.assertFalse(user_can_view_journal(self.supervisor_two, self.only_first_journal))

        denied_pdf = self._login(self.supervisor_two).post(
            reverse("supervisor:journal_pdf", kwargs={"journal_pk": self.journals[0].pk}),
            {"pdf_password": "test1234"},
        )
        self.assertEqual(denied_pdf.status_code, 403)
        denied_new = self._login(self.supervisor_one).post(
            reverse("supervisor:journal_pdf", kwargs={"journal_pk": cohort_two_journal.pk}),
            {"pdf_password": "test1234"},
        )
        self.assertEqual(denied_new.status_code, 403)

        self.counselor.counselor_profile.refresh_from_db()
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.case_a.refresh_from_db()
        self.case_b.refresh_from_db()
        self.assertEqual(self.counselor.counselor_profile.cohort, 2)
        self.assertEqual(self.first.status, TrainingEnrollmentStatus.COMPLETED)
        self.assertEqual(self.second.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(self.case_a.training_enrollment_id, self.first.pk)
        self.assertEqual(self.case_b.training_enrollment_id, self.first.pk)


class MismatchedProfileCohortRosterTests(TestCase):
    """Enrollment가 하나인데 프로필 기수가 달라도 프로필 기수를 만들지 않는다."""

    def setUp(self):
        self.counselor = _create_counselor("불일치상담", cohort=2)
        self.enrollment = TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        self.linked_case = _create_case(self.counselor, _create_client("연재담"), "CASE-MISMATCH-1")
        self.linked_case.training_enrollment = self.enrollment
        self.linked_case.save(update_fields=["training_enrollment"])
        self.linked_journal = _create_journal(self.linked_case, self.counselor, session_number=1)
        self.linked_journal.subjective = "연결일지비밀"
        self.linked_journal.save(update_fields=["subjective", "updated_at"])
        self.orphan_case = _create_case(self.counselor, _create_client("미재담"), "CASE-MISMATCH-ORPHAN")
        self.orphan_journal = _create_journal(self.orphan_case, self.counselor, session_number=1)
        self.orphan_journal.subjective = "미연결일지비밀"
        self.orphan_journal.save(update_fields=["subjective", "updated_at"])
        self.supervisor_one = _supervisor("sv-mismatch-1@example.com", "일담당", [1])
        self.supervisor_two = _supervisor("sv-mismatch-2@example.com", "이담당", [2])
        self.detail_url = reverse(
            "supervisor:counselor_journals",
            kwargs={"counselor_id": self.counselor.pk},
        )

    def _login(self, user):
        http = HttpClient()
        http.force_login(user)
        return http

    def test_profile_cohort_does_not_create_a_roster_or_reveal_records(self):
        both = _supervisor("sv-mismatch-both@example.com", "복수담당", [1, 2])
        listed = self._login(both).get(reverse("supervisor:cohort_journals"))
        body = listed.content.decode()
        first = _cohort_section(body, "1기")
        self.assertIn("불일치상담", first)
        self.assertIn("상담일지 1건", first)
        self.assertNotIn("상담일지 2건", first)
        self.assertNotIn(">2기</h2>", body)

        one_only = self._login(self.supervisor_one).get(reverse("supervisor:cohort_journals"))
        self.assertContains(one_only, "불일치상담")
        self.assertContains(one_only, "상담일지 1건")
        self.assertNotContains(one_only, "2기")
        two_only = self._login(self.supervisor_two).get(reverse("supervisor:cohort_journals"))
        self.assertNotContains(two_only, "불일치상담")
        self.assertContains(two_only, "등록된 상담사가 없습니다.")

        detail = self._login(self.supervisor_one).get(f"{self.detail_url}?cohort=1")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "연*담")
        self.assertNotContains(detail, "미*담")
        self.assertNotContains(detail, "연결일지비밀")
        self.assertNotContains(detail, "미연결일지비밀")
        self.assertEqual(
            self._login(self.supervisor_two).get(f"{self.detail_url}?cohort=2").status_code,
            403,
        )
        self.assertEqual(
            self._login(self.supervisor_one).get(f"{self.detail_url}?cohort=2").status_code,
            403,
        )
        self.assertEqual(
            self._login(self.supervisor_two).get(f"{self.detail_url}?cohort=1").status_code,
            403,
        )

        self.assertTrue(user_can_view_journal(self.supervisor_one, self.linked_journal))
        self.assertTrue(user_can_download_journal_pdf(self.supervisor_one, self.linked_journal))
        self.assertFalse(user_can_view_journal(self.supervisor_two, self.linked_journal))
        self.assertFalse(user_can_download_journal_pdf(self.supervisor_two, self.linked_journal))
        self.assertFalse(user_can_view_journal(self.supervisor_one, self.orphan_journal))
        self.assertFalse(user_can_download_journal_pdf(self.supervisor_one, self.orphan_journal))
        self.assertFalse(user_can_view_journal(self.supervisor_two, self.orphan_journal))
        denied = self._login(self.supervisor_two).post(
            reverse("supervisor:journal_pdf", kwargs={"journal_pk": self.linked_journal.pk}),
            {"pdf_password": "test1234"},
        )
        self.assertEqual(denied.status_code, 403)
        denied_orphan = self._login(self.supervisor_one).post(
            reverse("supervisor:journal_pdf", kwargs={"journal_pk": self.orphan_journal.pk}),
            {"pdf_password": "test1234"},
        )
        self.assertEqual(denied_orphan.status_code, 403)
