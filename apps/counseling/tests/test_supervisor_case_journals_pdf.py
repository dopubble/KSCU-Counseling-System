"""수퍼바이저 상담사례 상담일지 일괄 PDF."""

from datetime import datetime
from io import BytesIO

from pypdf import PdfReader
from django.contrib.auth import get_user_model
from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import SupervisorProfile, UserRole, UserStatus
from apps.counseling.models import (
    CounselingMethod,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.counseling.tests.test_journal_permissions import (
    _create_case,
    _create_client,
    _create_counselor,
    _create_journal,
)
from apps.sessions_app.models import CounselingJournal

User = get_user_model()


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


def _enroll(counselor, cohort, slot):
    return TrainingEnrollment.objects.create(
        counselor=counselor,
        cohort=cohort,
        status=TrainingEnrollmentStatus.ACTIVE,
        active_slot=slot,
    )


def _pdf_text(content, password):
    reader = PdfReader(BytesIO(content))
    if reader.is_encrypted:
        reader.decrypt(password)
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    return text, len(reader.pages)


class SupervisorCaseJournalsPdfTests(TestCase):
    password = "pdf-pass"

    def setUp(self):
        self.counselor = _create_counselor("김수련", cohort=2)
        self.other_counselor = _create_counselor("다른상담", cohort=1)
        self.cohort_one = _enroll(self.counselor, 1, 1)
        self.cohort_two = _enroll(self.counselor, 2, 2)
        self.client_one = _create_client("홍길동")
        self.client_two = _create_client("김영희")
        self.other_client = _create_client("박민수")
        self.case = _create_case(self.counselor, self.client_one, "CASE-BULK-1")
        self.case.training_enrollment = self.cohort_one
        self.case.save(update_fields=["training_enrollment"])
        self.other_case = _create_case(self.counselor, self.client_two, "CASE-BULK-2")
        self.other_case.training_enrollment = self.cohort_one
        self.other_case.save(update_fields=["training_enrollment"])
        self.cohort_two_case = _create_case(self.counselor, self.client_one, "CASE-BULK-3")
        self.cohort_two_case.training_enrollment = self.cohort_two
        self.cohort_two_case.save(update_fields=["training_enrollment"])
        self.foreign_case = _create_case(self.other_counselor, self.other_client, "CASE-BULK-9")
        self.foreign_case.training_enrollment = _enroll(self.other_counselor, 1, 1)
        self.foreign_case.save(update_fields=["training_enrollment"])
        self.session_at = timezone.make_aware(datetime(2026, 3, 2, 10, 30))
        self.journals = []
        for number in (1, 3):
            journal = _create_journal(self.case, self.counselor, session_number=number)
            journal.session_datetime = self.session_at
            journal.counseling_method = CounselingMethod.REMOTE
            journal.session_categories = ["개인상담"]
            journal.subjective = f"일번내용{number} ★상담&<기록>"
            journal.objective = f"관찰{number}"
            journal.assessment = f"평가{number}"
            journal.plan = f"계획{number}"
            journal.save()
            self.journals.append(journal)
        self.draft = CounselingJournal.objects.create(
            case=self.case,
            counselor=self.counselor,
            session_number=4,
            is_draft=True,
            subjective="초안비밀내용XYZ",
        )
        self.other_journal = _create_journal(self.other_case, self.counselor, session_number=1)
        self.other_journal.subjective = "다른내담자비밀XYZ"
        self.other_journal.save(update_fields=["subjective", "updated_at"])
        self.cohort_two_journal = _create_journal(
            self.cohort_two_case, self.counselor, session_number=1
        )
        self.cohort_two_journal.subjective = "이기수비밀XYZ"
        self.cohort_two_journal.save(update_fields=["subjective", "updated_at"])
        self.foreign_journal = _create_journal(
            self.foreign_case, self.other_counselor, session_number=1
        )
        self.foreign_journal.subjective = "다른상담사비밀XYZ"
        self.foreign_journal.save(update_fields=["subjective", "updated_at"])
        self.empty_case = _create_case(self.counselor, self.client_one, "CASE-BULK-0")
        self.empty_case.training_enrollment = self.cohort_one
        self.empty_case.save(update_fields=["training_enrollment"])
        self.supervisor = _supervisor("sv-bulk@example.com", "담당수퍼", [1])
        self.other_supervisor = _supervisor("sv-bulk-2@example.com", "다른수퍼", [2])
        self.http = HttpClient()

    def _url(self, case=None, counselor=None, cohort=1):
        return (
            reverse(
                "supervisor:case_journals_pdf",
                kwargs={
                    "counselor_id": (counselor or self.counselor).pk,
                    "case_pk": (case or self.case).pk,
                },
            )
            + f"?cohort={cohort}"
        )

    def _download(self, user, case=None, counselor=None, cohort=1):
        self.http.force_login(user)
        return self.http.post(
            self._url(case=case, counselor=counselor, cohort=cohort),
            {"pdf_password": self.password, "next": "/supervisor/journals/"},
        )

    def test_button_is_on_the_case_list_and_session_view_remains(self):
        self.http.force_login(self.supervisor)
        response = self.http.get(
            reverse("supervisor:counselor_journals", kwargs={"counselor_id": self.counselor.pk})
            + "?cohort=1"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "상담일지 일괄 다운로드")
        self.assertContains(response, self._url())
        self.assertContains(response, "PDF 보기")
        self.assertContains(response, 'addEventListener("click"')
        self.assertNotContains(response, "show.bs.modal")
        self.assertContains(
            response,
            reverse("supervisor:journal_pdf", kwargs={"journal_pk": self.journals[0].pk}),
        )
        self.assertNotContains(response, "초안비밀내용XYZ")
        self.assertNotContains(response, "이기수비밀XYZ")
        self.assertNotContains(response, "다른내담자비밀XYZ")
        self.assertNotContains(response, self._url(case=self.empty_case))

    def test_one_case_pdf_contains_completed_sessions_in_order(self):
        response = self._download(self.supervisor)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn("journals_c1_CASE-BULK-1.pdf", response["Content-Disposition"])
        self.assertNotIn("홍길동", response["Content-Disposition"])
        text, pages = _pdf_text(response.content, self.password)
        self.assertGreaterEqual(pages, 3)
        self.assertIn("1기", text)
        self.assertIn("김수련", text)
        self.assertIn("홍*동", text)
        self.assertNotIn("홍길동", text)
        self.assertIn("CASE-BULK-1", text)
        self.assertIn("2건", text)
        self.assertIn("1회기 상담일지", text)
        self.assertIn("3회기 상담일지", text)
        self.assertNotIn("2회기 상담일지", text)
        self.assertLess(text.index("일번내용1"), text.index("일번내용3"))
        self.assertIn("비대면", text)
        self.assertIn("개인상담", text)
        self.assertIn("2026-03-02 10:30", text)
        self.assertIn("관찰1", text)
        self.assertIn("평가3", text)
        self.assertIn("계획3", text)
        self.assertIn("★상담&<기록>", text)
        self.assertNotIn("초안비밀내용XYZ", text)
        self.assertNotIn("다른내담자비밀XYZ", text)
        self.assertNotIn("이기수비밀XYZ", text)
        self.assertNotIn("다른상담사비밀XYZ", text)
        self.assertNotIn("CASE-BULK-2", text)
        self.assertNotIn("CASE-BULK-3", text)

    def test_counts_gap_long_text_and_special_characters(self):
        from apps.sessions_app.pdf import build_case_journals_pdf

        single = build_case_journals_pdf(
            self.case,
            [self.journals[0]],
            cohort_label="1기",
            counselor_name="김수련",
            client_label="홍*동",
            user_password=self.password,
        )
        text, pages = _pdf_text(single, self.password)
        self.assertGreaterEqual(pages, 2)
        self.assertIn("1회기", text)
        self.assertNotIn("일번내용3", text)

        five = []
        for number in range(1, 6):
            journal = _create_journal(self.other_case, self.counselor, session_number=number + 10)
            journal.subjective = f"다섯표시{number}"
            journal.save(update_fields=["subjective", "updated_at"])
            five.append(journal)
        text, pages = _pdf_text(
            build_case_journals_pdf(
                self.other_case,
                five,
                cohort_label="1기",
                counselor_name="김수련",
                client_label="김*희",
                user_password=self.password,
            ),
            self.password,
        )
        self.assertGreaterEqual(pages, 6)
        self.assertLess(text.index("다섯표시1"), text.index("다섯표시5"))

        ten = []
        for number in range(1, 11):
            journal = _create_journal(
                self.cohort_two_case, self.counselor, session_number=number + 20
            )
            journal.subjective = f"열표시{number}"
            journal.save(update_fields=["subjective", "updated_at"])
            ten.append(journal)
        text, pages = _pdf_text(
            build_case_journals_pdf(
                self.cohort_two_case,
                ten,
                cohort_label="2기",
                counselor_name="김수련",
                client_label="홍*동",
                user_password=self.password,
            ),
            self.password,
        )
        self.assertGreaterEqual(pages, 11)
        self.assertLess(text.index("열표시1"), text.index("열표시10"))
        self.assertNotIn("다섯표시1", text)

        long_journal = _create_journal(self.case, self.counselor, session_number=8)
        long_journal.subjective = ("긴상담내용입니다. " * 400) + "끝표시LONG"
        long_journal.save(update_fields=["subjective", "updated_at"])
        text, pages = _pdf_text(
            build_case_journals_pdf(
                self.case,
                [long_journal],
                cohort_label="1기",
                counselor_name="김수련",
                client_label="홍*동",
                user_password=self.password,
            ),
            self.password,
        )
        self.assertGreaterEqual(pages, 3)
        self.assertIn("끝표시LONG", text)
        self.assertIn("긴상담내용입니다.", text)

    def test_empty_case_does_not_download(self):
        response = self._download(self.supervisor, case=self.empty_case)
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response["Content-Type"], "application/pdf")

    def test_other_supervisor_counselor_and_client_are_denied(self):
        self.assertEqual(self._download(self.other_supervisor).status_code, 403)
        self.assertEqual(self._download(self.counselor).status_code, 403)
        self.assertEqual(self._download(self.client_one).status_code, 403)
        anonymous = HttpClient()
        response = anonymous.post(self._url(), {"pdf_password": self.password})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_other_cohort_and_other_case_urls_are_denied(self):
        self.assertEqual(
            self._download(self.supervisor, case=self.cohort_two_case, cohort=1).status_code,
            403,
        )
        self.assertEqual(
            self._download(self.supervisor, case=self.case, cohort=2).status_code,
            403,
        )
        self.assertEqual(
            self._download(
                self.supervisor,
                case=self.foreign_case,
                counselor=self.counselor,
                cohort=1,
            ).status_code,
            403,
        )
        foreign = self._download(
            self.supervisor,
            case=self.foreign_case,
            counselor=self.other_counselor,
            cohort=1,
        )
        foreign_text, _pages = _pdf_text(foreign.content, self.password)
        self.assertIn("다른상담사비밀XYZ", foreign_text)
        self.assertNotIn("일번내용1", foreign_text)
        self.assertNotIn("CASE-BULK-1", foreign_text)
        self.http.force_login(self.supervisor)
        response = self.http.get(self._url())
        self.assertEqual(response.status_code, 405)
