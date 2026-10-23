"""수퍼바이저 기수별 상담사 목록과 상담사별 완료 일지 화면."""

from datetime import datetime

from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import SupervisorProfile, User, UserRole, UserStatus
from apps.counseling.tests.test_journal_permissions import (
    _create_case,
    _create_client,
    _create_counselor,
    _create_journal,
)
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.sessions_app.models import CounselingJournal


def _supervisor(email: str, name: str, cohorts: list[int]) -> User:
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


class SupervisorCounselorJournalPageTests(TestCase):
    def setUp(self):
        self.counselor = _create_counselor("김상담", cohort=1)
        self.peer = _create_counselor("이상담", cohort=1)
        self.other = _create_counselor("최상담", cohort=2)
        self.unapproved = _create_counselor("미승인상담", cohort=1)
        self.unapproved.counselor_profile.is_approved = False
        self.unapproved.counselor_profile.save(update_fields=["is_approved", "updated_at"])
        self.client_hong = _create_client("홍길동")
        self.client_kim = _create_client("김영희")
        self.case = _create_case(self.counselor, self.client_hong, "CASE-PAGE-1")
        self.peer_case = _create_case(self.peer, self.client_kim, "CASE-PAGE-2")
        self.other_case = _create_case(self.other, _create_client("박민수"), "CASE-PAGE-3")
        self.session_at = timezone.make_aware(datetime(2024, 3, 15, 14, 30))
        self.journal = _create_journal(self.case, self.counselor, session_number=1)
        self.journal.session_datetime = self.session_at
        self.journal.subjective = "비밀상담내용XYZ"
        self.journal.save(update_fields=["session_datetime", "subjective", "updated_at"])
        self.draft = CounselingJournal.objects.create(
            case=self.case,
            counselor=self.counselor,
            session_number=22,
            is_draft=True,
            subjective="초안비밀내용XYZ",
        )
        self.late = _create_journal(self.case, self.counselor, session_number=21)
        self.peer_journal = _create_journal(self.peer_case, self.peer, session_number=1)
        self.other_journal = _create_journal(self.other_case, self.other, session_number=1)
        self.supervisor = _supervisor("sv-page-1@example.com", "담당수퍼", [1])
        self.http = HttpClient()
        self.http.force_login(self.supervisor)

    def test_assigned_cohort_lists_only_that_cohort(self):
        response = self.http.get(reverse("supervisor:cohort_journals"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "김상담")
        self.assertContains(response, "이상담")
        self.assertContains(response, "1기")
        self.assertNotContains(response, "최상담")
        self.assertNotContains(response, "2기")
        self.assertNotContains(response, "미승인상담")
        self.assertNotContains(response, "홍길동")
        self.assertNotContains(response, "홍*동")
        self.assertContains(response, "상담일지 2건")
        self.assertContains(
            response,
            reverse("supervisor:counselor_journals", kwargs={"counselor_id": self.counselor.pk}),
        )

    def test_multiple_assigned_cohorts_are_sectioned(self):
        both = _supervisor("sv-page-both@example.com", "복수담당", [1, 2])
        http = HttpClient()
        http.force_login(both)
        response = http.get(reverse("supervisor:cohort_journals"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1기")
        self.assertContains(response, "2기")
        self.assertContains(response, "김상담")
        self.assertContains(response, "최상담")

    def test_admin_sees_counselors_in_every_cohort(self):
        admin = User.objects.create_user(
            email="admin-page@example.com",
            password="pass12345",
            name="관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        http = HttpClient()
        http.force_login(admin)
        response = http.get(reverse("supervisor:cohort_journals"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "김상담")
        self.assertContains(response, "최상담")
        self.assertContains(response, "전체 기수")

    def test_other_cohort_counselor_url_is_forbidden(self):
        url = reverse(
            "supervisor:counselor_journals",
            kwargs={"counselor_id": self.other.pk},
        )
        response = self.http.get(url)
        self.assertEqual(response.status_code, 403)

    def test_counselor_cannot_open_supervisor_journal_urls(self):
        http = HttpClient()
        http.force_login(self.counselor)
        list_response = http.get(reverse("supervisor:cohort_journals"))
        detail_response = http.get(
            reverse(
                "supervisor:counselor_journals",
                kwargs={"counselor_id": self.peer.pk},
            )
        )
        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(detail_response.status_code, 403)

    def test_detail_shows_completed_journals_for_selected_counselor_only(self):
        response = self.http.get(
            reverse(
                "supervisor:counselor_journals",
                kwargs={"counselor_id": self.counselor.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "[1기] 김상담 상담일지")
        self.assertContains(response, "홍*동")
        self.assertNotContains(response, "홍길동")
        self.assertContains(response, "1회기")
        self.assertContains(response, "21회기")
        self.assertNotContains(response, "22회기")
        self.assertContains(response, "작성완료")
        self.assertContains(response, "2024-03-15 14:30")
        self.assertNotContains(response, "김영희")
        self.assertNotContains(response, "김*희")
        self.assertNotContains(response, "비밀상담내용XYZ")
        self.assertNotContains(response, "초안비밀내용XYZ")
        self.assertNotContains(response, "성별")
        self.assertNotContains(response, "생년월일")
        self.assertNotContains(response, "직업")
        pdf_url = reverse("supervisor:journal_pdf", kwargs={"journal_pk": self.journal.pk})
        late_pdf_url = reverse("supervisor:journal_pdf", kwargs={"journal_pk": self.late.pk})
        self.assertContains(response, pdf_url)
        self.assertContains(response, late_pdf_url)
        self.assertContains(response, reverse("supervisor:cohort_journals"))
        self.assertContains(response, 'class="nav-link active"')

    def test_empty_journal_list_and_empty_counselor_list(self):
        empty_counselor = _create_counselor("빈상담", cohort=3)
        supervisor = _supervisor("sv-page-empty@example.com", "빈담당", [3])
        http = HttpClient()
        http.force_login(supervisor)
        list_response = http.get(reverse("supervisor:cohort_journals"))
        self.assertContains(list_response, "빈상담")
        detail = http.get(
            reverse(
                "supervisor:counselor_journals",
                kwargs={"counselor_id": empty_counselor.pk},
            )
        )
        self.assertContains(detail, "아직 작성 완료된 상담일지가 없습니다.")

        none = _supervisor("sv-page-none@example.com", "무담당상담사", [9])
        http.force_login(none)
        response = http.get(reverse("supervisor:cohort_journals"))
        self.assertContains(response, "등록된 상담사가 없습니다.")

    def test_session_date_falls_back_to_appointment(self):
        scheduled = timezone.make_aware(datetime(2024, 6, 12, 10, 0))
        appointment = Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_hong,
            scheduled_at=scheduled,
            status=AppointmentStatus.CONFIRMED,
            session_number=4,
        )
        journal = _create_journal(self.case, self.counselor, session_number=4)
        journal.session_datetime = None
        journal.appointment = appointment
        journal.save(update_fields=["session_datetime", "appointment", "updated_at"])
        response = self.http.get(
            reverse(
                "supervisor:counselor_journals",
                kwargs={"counselor_id": self.counselor.pk},
            )
        )
        self.assertContains(response, "2024-06-12 10:00")
        self.assertContains(response, "4회기")
