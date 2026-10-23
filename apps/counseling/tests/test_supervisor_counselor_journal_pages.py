"""수퍼바이저 기수별 상담사 목록과 상담사별 완료 일지 화면."""

from datetime import datetime

from django.db import connection
from django.test import Client as HttpClient, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import SupervisorProfile, User, UserRole, UserStatus
from apps.counseling.models import CaseStatus, CaseTerminationType
from apps.counseling.tests.test_journal_permissions import (
    _create_case,
    _create_client,
    _create_counselor,
    _create_journal,
)
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.sessions_app.models import (
    CounselingJournal,
    InitialCounselingRecord,
    TerminationCounselingRecord,
)


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
        self.assertContains(response, "사례 1건 | 완료 상담 2회기")
        self.assertContains(response, "홍*동")
        self.assertContains(response, "2회기 | 최근상담 2024-03-15 14:30")
        self.assertNotContains(response, "홍길동")
        self.assertContains(response, ">1회기<")
        self.assertContains(response, ">21회기<")
        self.assertNotContains(response, "22회기")
        self.assertNotContains(response, "accordion-collapse collapse show")
        self.assertNotContains(response, 'aria-expanded="true"')
        self.assertNotContains(response, "data-bs-parent")
        self.assertContains(response, "accordion-button collapsed")
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
        self.assertContains(response, ">4회기<")
        self.assertContains(response, "최근상담 2024-06-12 10:00")

    def test_cases_are_grouped_and_ordered_by_latest_session(self):
        other_client = _create_client("이수영")
        other_case = _create_case(self.counselor, other_client, "CASE-PAGE-4")
        later = _create_journal(other_case, self.counselor, session_number=2)
        later.session_datetime = timezone.make_aware(datetime(2024, 10, 18, 11, 0))
        later.save(update_fields=["session_datetime", "updated_at"])
        earlier = _create_journal(other_case, self.counselor, session_number=1)
        earlier.session_datetime = timezone.make_aware(datetime(2024, 1, 2, 9, 0))
        earlier.save(update_fields=["session_datetime", "updated_at"])

        response = self.http.get(
            reverse(
                "supervisor:counselor_journals",
                kwargs={"counselor_id": self.counselor.pk},
            )
        )
        body = response.content.decode()
        self.assertContains(response, "사례 2건 | 완료 상담 4회기")
        self.assertContains(response, f'id="caseCollapse{self.case.pk}"')
        self.assertContains(response, f'id="caseCollapse{other_case.pk}"')
        self.assertLess(body.index("이*영"), body.index("홍*동"))
        header = _header(body, other_case.pk)
        self.assertIn("2회기 | 최근상담 2024-10-18 11:00", header)
        self.assertNotIn("22회기", header)
        collapse_at = body.index(f'id="caseCollapse{other_case.pk}"')
        section = body[collapse_at:]
        self.assertNotIn("collapse show", section[:section.index("<tbody>")])
        table = section[section.index("<tbody>"):]
        self.assertLess(table.index(">1회기<"), table.index(">2회기<"))
        self.assertNotIn("<th scope=\"col\">내담자</th>", body)

    def test_same_client_name_stays_separate_cases(self):
        twin = User.objects.create_user(
            email="twin-hong@example.com",
            password="pass12345",
            name="홍길동",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        twin_case = _create_case(self.counselor, twin, "CASE-PAGE-5")
        twin_journal = _create_journal(twin_case, self.counselor, session_number=1)
        twin_journal.session_datetime = timezone.make_aware(datetime(2023, 5, 1, 9, 0))
        twin_journal.save(update_fields=["session_datetime", "updated_at"])

        response = self.http.get(
            reverse(
                "supervisor:counselor_journals",
                kwargs={"counselor_id": self.counselor.pk},
            )
        )
        body = response.content.decode()
        self.assertContains(response, f'id="caseCollapse{self.case.pk}"')
        self.assertContains(response, f'id="caseCollapse{twin_case.pk}"')
        self.assertContains(response, "사례 2건 | 완료 상담 3회기")
        self.assertNotContains(response, "홍길동")
        self.assertIn("2회기 | 최근상담 2024-03-15 14:30", _header(body, self.case.pk))
        self.assertIn("1회기 | 최근상담 2023-05-01 09:00", _header(body, twin_case.pk))


def _case_section(body: str, case_id) -> str:
    start = body.index(f'id="caseHeading{case_id}"')
    nxt = body.find("accordion-item", start + 1)
    if nxt == -1:
        return body[start:]
    return body[start:nxt]


def _header(body: str, case_id) -> str:
    section = _case_section(body, case_id)
    return section[:section.index(f'id="caseCollapse{case_id}"')]


def _row(body: str, case_id, kind: str) -> str:
    section = _case_section(body, case_id)
    start = section.index(f'data-row="{kind}"')
    return section[start:section.index("</tr>", start)]


class CaseRecordSummaryTests(TestCase):
    def setUp(self):
        self.counselor = _create_counselor("기록상담", cohort=1)
        self.client_user = _create_client("기록내담")
        self.case = _create_case(self.counselor, self.client_user, "CASE-REC-1")
        self.journal = _create_journal(self.case, self.counselor, session_number=1)
        self.journal.session_datetime = timezone.make_aware(datetime(2024, 7, 3, 10, 0))
        self.journal.save(update_fields=["session_datetime", "updated_at"])
        _create_journal(self.case, self.counselor, session_number=2)
        self.supervisor = _supervisor("sv-rec@example.com", "기록수퍼", [1])
        self.http = HttpClient()
        self.http.force_login(self.supervisor)

    def _page(self):
        response = self.http.get(
            reverse(
                "supervisor:counselor_journals",
                kwargs={"counselor_id": self.counselor.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        return response

    def test_completed_initial_record_links_supervisor_detail(self):
        started = timezone.make_aware(datetime(2024, 6, 22, 16, 30))
        record = InitialCounselingRecord.objects.create(
            case=self.case,
            counselor=self.counselor,
            is_draft=False,
            session_start_datetime=started,
        )
        response = self._page()
        detail_url = reverse(
            "supervisor:initial_record_detail",
            kwargs={"record_pk": record.pk},
        )
        body = response.content.decode()
        row = _row(body, self.case.pk, "initial")
        self.assertIn("초기상담기록지", row)
        self.assertIn("2024-06-22 16:30", row)
        self.assertIn("작성완료", row)
        self.assertIn("보기", row)
        self.assertIn(detail_url, row)
        self.assertNotIn("counselor", detail_url)
        self.assertNotContains(response, "사례기록")
        self.assertNotContains(response, "termination-record")
        self.assertNotContains(response, "/initial-record/")

        other = _supervisor("sv-rec-other@example.com", "다른기수", [2])
        http = HttpClient()
        http.force_login(other)
        denied = http.get(detail_url)
        self.assertEqual(denied.status_code, 403)

    def test_missing_or_draft_initial_record_is_unwritten(self):
        missing = self._page()
        missing_row = _row(missing.content.decode(), self.case.pk, "initial")
        self.assertIn("초기상담기록지", missing_row)
        self.assertIn("—", missing_row)
        self.assertIn("미작성", missing_row)
        self.assertNotIn("보기", missing_row)

        InitialCounselingRecord.objects.create(
            case=self.case,
            counselor=self.counselor,
            is_draft=True,
            session_start_datetime=timezone.make_aware(datetime(2024, 6, 22, 16, 30)),
        )
        draft = self._page()
        draft_row = _row(draft.content.decode(), self.case.pk, "initial")
        self.assertIn("미작성", draft_row)
        self.assertNotIn("2024-06-22 16:30", draft_row)
        self.assertNotIn("보기", draft_row)
        self.assertNotIn("initial-records/", draft_row)

    def test_termination_status_follows_completed_record_and_case_status(self):
        open_body = self._page().content.decode()
        self.assertIn(">진행중</span>", _header(open_body, self.case.pk))
        open_row = _row(open_body, self.case.pk, "termination")
        self.assertIn("종결기록지", open_row)
        self.assertIn("종결 전", open_row)
        self.assertNotIn("href", open_row)

        self.case.status = CaseStatus.CLOSED
        self.case.save(update_fields=["status"])
        closed_body = self._page().content.decode()
        closed_header = _header(closed_body, self.case.pk)
        self.assertIn(">종결</span>", closed_header)
        self.assertNotIn("종결 전", closed_header)
        closed_row = _row(closed_body, self.case.pk, "termination")
        self.assertIn("미작성", closed_row)

        TerminationCounselingRecord.objects.create(
            case=self.case,
            counselor=self.counselor,
            is_draft=True,
        )
        draft_row = _row(self._page().content.decode(), self.case.pk, "termination")
        self.assertIn("미작성", draft_row)
        self.assertNotIn("작성완료", draft_row)

        self.case.status = CaseStatus.ACTIVE
        self.case.save(update_fields=["status"])
        active_body = self._page().content.decode()
        self.assertIn(">진행중</span>", _header(active_body, self.case.pk))
        active_draft = _row(active_body, self.case.pk, "termination")
        self.assertIn("종결 전", active_draft)

        record = self.case.termination_counseling_record
        record.is_draft = False
        record.save(update_fields=["is_draft", "updated_at"])
        done_body = self._page().content.decode()
        done_row = _row(done_body, self.case.pk, "termination")
        self.assertIn("작성완료", done_row)
        self.assertNotIn("href", done_row)
        self.assertIn(">진행중</span>", _header(done_body, self.case.pk))

    def test_summary_journal_count_matches_completed_journals(self):
        CounselingJournal.objects.create(
            case=self.case,
            counselor=self.counselor,
            session_number=9,
            is_draft=True,
        )
        body = self._page().content.decode()
        self.assertIn("2회기 | 최근상담", _header(body, self.case.pk))
        self.assertContains(self._page(), "완료 상담 2회기")
        section = _case_section(body, self.case.pk)
        self.assertLess(section.index("초기상담기록지"), section.index(">1회기<"))
        self.assertLess(section.index(">1회기<"), section.index(">2회기<"))
        self.assertLess(section.index(">2회기<"), section.index("종결기록지"))
        self.assertNotIn(">9회기<", section)

    def test_record_relations_do_not_add_a_query_per_case(self):
        def build(name, count):
            counselor = _create_counselor(name, cohort=1)
            for index in range(count):
                client = _create_client(f"{name}내담{index}")
                case = _create_case(counselor, client, f"CASE-{name}-{index}")
                _create_journal(case, counselor, session_number=1)
                InitialCounselingRecord.objects.create(
                    case=case,
                    counselor=counselor,
                    is_draft=False,
                )
                TerminationCounselingRecord.objects.create(
                    case=case,
                    counselor=counselor,
                    is_draft=False,
                )
            return counselor

        one = build("한사례", 1)
        many = build("세사례", 3)

        def query_count(counselor):
            with CaptureQueriesContext(connection) as captured:
                response = self.http.get(
                    reverse(
                        "supervisor:counselor_journals",
                        kwargs={"counselor_id": counselor.pk},
                    )
                )
            self.assertEqual(response.status_code, 200)
            return len(captured)

        self.assertEqual(query_count(one), query_count(many))

    def test_header_labels_follow_status_and_termination_type(self):
        labels = (
            (CaseStatus.ACTIVE, "", "진행중"),
            (CaseStatus.ON_HOLD, "", "휴지"),
            (CaseStatus.TRANSFERRED, "", "이관"),
            (CaseStatus.CLOSED, CaseTerminationType.NORMAL, "정상종결"),
            (CaseStatus.CLOSED, CaseTerminationType.EARLY, "조기종결"),
            (CaseStatus.CLOSED, "", "종결"),
        )
        for status, termination_type, label in labels:
            self.case.status = status
            self.case.termination_type = termination_type
            self.case.save(update_fields=["status", "termination_type"])
            header = _header(self._page().content.decode(), self.case.pk)
            self.assertIn(f">{label}</span>", header)
            self.assertIn("pe-none", header)

    def test_early_termination_header_is_independent_of_missing_record(self):
        self.case.status = CaseStatus.CLOSED
        self.case.termination_type = CaseTerminationType.EARLY
        self.case.save(update_fields=["status", "termination_type"])
        body = self._page().content.decode()
        self.assertIn(">조기종결</span>", _header(body, self.case.pk))
        row = _row(body, self.case.pk, "termination")
        self.assertIn("종결기록지", row)
        self.assertIn("미작성", row)
        self.assertNotIn("href", row)

    def test_accordion_collapse_flags_remain(self):
        body = self._page().content.decode()
        self.assertNotIn("사례기록", body)
        self.assertNotIn("accordion-collapse collapse show", body)
        self.assertNotIn("data-bs-parent", body)
        self.assertNotIn("성별", body)
        self.assertNotIn("생년월일", body)
        self.assertNotIn("직업", body)
