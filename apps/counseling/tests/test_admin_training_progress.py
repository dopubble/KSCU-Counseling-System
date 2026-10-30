"""관리자 수련현황 통합 목록·상세."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client as HttpClient, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
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
)
from apps.counseling.training_progress import (
    get_cohort_counselors_training_progress,
    get_counselor_training_progress,
)
from apps.documents.models import ConsentDocType, ConsentDocument
from apps.sessions_app.models import CounselingJournal, TerminationCounselingRecord


def _user(email, name, role, *, status=UserStatus.ACTIVE):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=status,
    )


def _counselor(name, cohort):
    user = _user(f"{name}@example.com", name, UserRole.COUNSELOR)
    CounselorProfile.objects.update_or_create(
        user=user,
        defaults={"cohort": cohort, "is_approved": True},
    )
    return user


def _client_user(name):
    return _user(f"{name}@client.example.com", name, UserRole.CLIENT)


def _case(counselor, client, case_number, *, total_sessions=10, remaining_sessions=10):
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
        case_number=case_number,
        status=CaseStatus.ACTIVE,
        counseling_method=CounselingMethod.IN_PERSON,
        total_sessions=total_sessions,
        remaining_sessions=remaining_sessions,
    )


def _intake(case, *, method=None):
    doc = ConsentDocument.objects.create(
        client=case.client,
        application=case.application,
        doc_type=ConsentDocType.INTAKE,
        file="consents/intake.pdf",
        uploaded_by=case.counselor,
    )
    if method is not None:
        Case.objects.filter(pk=case.pk).update(intake_counseling_method=method)
        case.intake_counseling_method = method
    return doc


def _journal(case, session_number, *, is_draft=False, counseling_method=None):
    return CounselingJournal.objects.create(
        case=case,
        counselor=case.counselor,
        session_number=session_number,
        is_draft=is_draft,
        counseling_method=counseling_method,
        subjective="S",
        objective="O",
        assessment="A",
        plan="P",
    )


def _sessions(cohort, status, count, *, start=1, duration_minutes=60, counseling_method=None):
    return [
        SupervisionSession.objects.create(
            cohort=cohort,
            session_number=start + offset,
            scheduled_at=timezone.now(),
            status=status,
            duration_minutes=duration_minutes,
            counseling_method=counseling_method,
        )
        for offset in range(count)
    ]


def _presentation(author, title, *, case=None, cohort=1):
    return CasePresentationPost.objects.create(
        cohort=cohort,
        author=author,
        case=case,
        title=title,
        file=SimpleUploadedFile(f"{title}.pdf", b"%PDF-1.4"),
    )


PROGRESS_COMPARE_KEYS = (
    "intake_required_count",
    "intake_completed_count",
    "counseling_assigned_count",
    "journal_completed_count",
    "termination_required_count",
    "termination_completed_count",
    "supervision_total_count",
    "supervision_completed_count",
    "supervision_attended_count",
    "supervision_attendance_percent",
    "supervision_attended_minutes",
    "case_report_required_count",
    "case_report_submitted_count",
    "public_presentation_participation_count",
    "training_completed_minutes",
    "training_completed_label",
    "training_in_person_minutes",
    "training_remote_minutes",
    "training_unknown_minutes",
    "training_method_summary_label",
)


class AdminTrainingProgressAccessTests(TestCase):
    def setUp(self):
        self.admin = _user("admin-tp@example.com", "관리자", UserRole.ADMIN)
        self.counselor = _counselor("목록상담", 1)
        self.url = reverse("admin_panel:training_progress")
        self.detail_url = reverse(
            "admin_panel:counselor_training_progress_detail",
            kwargs={"counselor_id": self.counselor.pk},
        )
        self.http = HttpClient()

    def test_admin_can_open_list_and_detail(self):
        self.http.force_login(self.admin)
        listing = self.http.get(self.url)
        detail = self.http.get(self.detail_url)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(listing, "수련현황")
        self.assertContains(listing, "목록상담")
        self.assertContains(detail, "목록상담 상담사 수련현황")

    def test_non_admin_roles_are_denied(self):
        for role, name in (
            (UserRole.COUNSELOR, "상담거부"),
            (UserRole.SUPERVISOR, "수퍼거부"),
            (UserRole.CLIENT, "내담거부"),
        ):
            user = _user(f"{name}@example.com", name, role)
            if role == UserRole.COUNSELOR:
                CounselorProfile.objects.update_or_create(
                    user=user, defaults={"cohort": 1, "is_approved": True}
                )
            http = HttpClient()
            http.force_login(user)
            self.assertEqual(http.get(self.url).status_code, 403)
            self.assertEqual(http.get(self.detail_url).status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        response = self.http.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)


class AdminTrainingProgressListTests(TestCase):
    def setUp(self):
        self.admin = _user("admin-list@example.com", "관리자", UserRole.ADMIN)
        self.kim = _counselor("김상담", 1)
        self.lee = _counselor("이상담", 1)
        self.park = _counselor("박상담", 2)
        self.http = HttpClient()
        self.http.force_login(self.admin)
        self.url = reverse("admin_panel:training_progress")

    def test_defaults_to_latest_cohort_and_sorts_by_name(self):
        response = self.http.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_cohort"], 2)
        self.assertContains(response, "박상담")
        self.assertNotContains(response, "김상담")

        cohort_one = self.http.get(self.url, {"cohort": "1"})
        body = cohort_one.content.decode()
        self.assertEqual(cohort_one.context["selected_cohort"], 1)
        self.assertContains(cohort_one, "김상담")
        self.assertContains(cohort_one, "이상담")
        self.assertNotContains(cohort_one, "박상담")
        self.assertLess(body.index("김상담"), body.index("이상담"))
        self.assertContains(cohort_one, ">1</td>")
        self.assertContains(cohort_one, ">2</td>")

    def test_detail_and_certificate_links(self):
        response = self.http.get(self.url, {"cohort": "1"})
        detail = reverse(
            "admin_panel:counselor_training_progress_detail",
            kwargs={"counselor_id": self.kim.pk},
        )
        certificate = reverse(
            "admin_panel:counselor_training_certificate",
            kwargs={"counselor_id": self.kim.pk},
        )
        self.assertContains(response, detail)
        self.assertContains(response, certificate)
        self.assertContains(response, 'target="_blank"')

    def test_empty_cohort_message(self):
        response = self.http.get(self.url, {"cohort": "99"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "해당 기수에 등록된 상담사가 없습니다.")

    def test_no_cohorts_message(self):
        CounselorProfile.objects.all().update(cohort=None)
        response = self.http.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "등록된 기수가 없습니다.")

    def test_nav_contains_training_progress(self):
        response = self.http.get(reverse("admin_panel:dashboard"))
        self.assertContains(response, "수련현황")
        self.assertContains(response, reverse("admin_panel:training_progress"))

    def test_admin_dashboard_shows_training_progress_card(self):
        response = self.http.get(reverse("admin_panel:dashboard"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        training_url = reverse("admin_panel:training_progress")
        self.assertIn("수련현황", body)
        self.assertIn("기수별 상담사 수련 진행상황 · 수련시간 확인", body)
        self.assertIn(f'href="{training_url}"', body)
        self.assertIn("사례발표 게시판", body)
        self.assertIn("상담사 명단", body)
        self.assertLess(
            body.index("수련현황"),
            body.index("사례발표 게시판"),
        )
        listing = self.http.get(training_url)
        self.assertEqual(listing.status_code, 200)

    def test_non_admin_home_hides_admin_training_progress_card(self):
        card_blurb = "기수별 상담사 수련 진행상황 · 수련시간 확인"
        for role, name in (
            (UserRole.COUNSELOR, "카드숨김상담"),
            (UserRole.SUPERVISOR, "카드숨김수퍼"),
            (UserRole.CLIENT, "카드숨김내담"),
        ):
            user = _user(f"{name}@example.com", name, role)
            if role == UserRole.COUNSELOR:
                CounselorProfile.objects.update_or_create(
                    user=user, defaults={"cohort": 1, "is_approved": True}
                )
            http = HttpClient()
            http.force_login(user)
            home = http.get(reverse("home"))
            self.assertEqual(home.status_code, 200)
            self.assertNotContains(home, card_blurb)
            denied = http.get(reverse("admin_panel:dashboard"))
            self.assertEqual(denied.status_code, 403)


class AdminTrainingProgressBatchParityTests(TestCase):
    def setUp(self):
        self.counselor = _counselor("일치상담", 3)
        self.other = _counselor("다른기수", 4)
        self.case_a = _case(
            self.counselor, _client_user("내담A"), "CASE-TP-A", total_sessions=10
        )
        self.case_b = _case(
            self.counselor, _client_user("내담B"), "CASE-TP-B", total_sessions=8
        )
        _intake(self.case_a)
        _journal(self.case_a, 1)
        _journal(self.case_a, 2)
        _journal(self.case_a, 3, is_draft=True)
        _journal(self.case_b, 1)
        TerminationCounselingRecord.objects.create(
            case=self.case_a, counselor=self.counselor, is_draft=False
        )
        TerminationCounselingRecord.objects.create(
            case=self.case_b, counselor=self.counselor, is_draft=True
        )

        completed = _sessions(3, SupervisionSessionStatus.COMPLETED, 8)
        _sessions(3, SupervisionSessionStatus.SCHEDULED, 1, start=9)
        _sessions(3, SupervisionSessionStatus.CANCELLED, 1, start=10)
        profile = self.counselor.counselor_profile
        for session in completed[:7]:
            SupervisionAttendance.objects.create(
                session=session,
                counselor=profile,
                status=SupervisionAttendanceStatus.PRESENT,
            )
        SupervisionAttendance.objects.create(
            session=completed[7],
            counselor=profile,
            status=SupervisionAttendanceStatus.ABSENT,
        )

        post = _presentation(self.counselor, "보고서", case=self.case_a, cohort=3)
        _presentation(self.counselor, "중복보고서", case=self.case_a, cohort=3)
        other_post = _presentation(self.other, "타기수글", case=None, cohort=4)
        CasePresentationComment.objects.create(
            post=post, author=self.counselor, content="의견1"
        )
        CasePresentationComment.objects.create(
            post=post, author=self.counselor, content="의견2"
        )
        CasePresentationComment.objects.create(
            post=other_post, author=self.counselor, content="다른글"
        )

    def test_batch_matches_individual_progress(self):
        individual = get_counselor_training_progress(self.counselor)
        batch_rows = get_cohort_counselors_training_progress(3)
        self.assertEqual(len(batch_rows), 1)
        batch = batch_rows[0]["progress"]

        self.assertEqual(individual["supervision_total_count"], 9)
        self.assertEqual(individual["supervision_completed_count"], 8)
        self.assertEqual(individual["supervision_attended_count"], 7)
        self.assertEqual(individual["supervision_attendance_percent"], 88)
        for key in PROGRESS_COMPARE_KEYS:
            self.assertEqual(batch[key], individual[key], key)

    def test_list_page_shows_compact_labels(self):
        admin = _user("admin-labels@example.com", "관리자", UserRole.ADMIN)
        http = HttpClient()
        http.force_login(admin)
        response = http.get(
            reverse("admin_panel:training_progress"), {"cohort": "3"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2/1건", count=2)
        self.assertContains(response, "18/3시간")
        self.assertNotContains(response, "3/18시간")
        self.assertContains(response, "9/7회")
        self.assertNotContains(response, "7/9회")
        self.assertContains(response, "1사례 완료")
        self.assertNotContains(response, "1/2사례")
        self.assertNotContains(response, "공개사례발표")
        self.assertContains(response, 'title="출석률 88%"')
        # intake+journals Case=IN_PERSON fallback → 대면 4시간; legacy supervision NULL → REMOTE 7시간
        self.assertContains(response, "대면 4시간 / 비대면 7시간")
        progress = response.context["rows"][0]["progress"]
        self.assertEqual(progress["supervision_attendance_percent"], 88)
        self.assertEqual(progress["training_in_person_minutes"], 240)
        self.assertEqual(progress["training_remote_minutes"], 420)
        self.assertEqual(progress["training_unknown_minutes"], 0)
        self.assertEqual(
            progress["training_in_person_minutes"]
            + progress["training_remote_minutes"]
            + progress["training_unknown_minutes"],
            progress["training_completed_minutes"],
        )


class AdminTrainingProgressQueryBudgetTests(TestCase):
    def _seed(self, count, cohort):
        counselors = []
        for index in range(count):
            counselor = _counselor(f"성능{cohort}-{index:02d}", cohort)
            case = _case(
                counselor,
                _client_user(f"성능내담{cohort}-{index}"),
                f"CASE-Q-{cohort}-{index}",
                total_sessions=5,
            )
            _intake(case)
            _journal(case, 1)
            counselors.append(counselor)
        completed = _sessions(cohort, SupervisionSessionStatus.COMPLETED, 2)
        for counselor in counselors:
            SupervisionAttendance.objects.create(
                session=completed[0],
                counselor=counselor.counselor_profile,
                status=SupervisionAttendanceStatus.PRESENT,
            )
        return counselors

    def test_query_count_does_not_grow_with_counselor_count(self):
        self._seed(1, cohort=51)
        with CaptureQueriesContext(connection) as one:
            one_rows = get_cohort_counselors_training_progress(51)
        self.assertEqual(len(one_rows), 1)
        one_count = len(one.captured_queries)

        self._seed(5, cohort=52)
        with CaptureQueriesContext(connection) as five:
            five_rows = get_cohort_counselors_training_progress(52)
        self.assertEqual(len(five_rows), 5)
        five_count = len(five.captured_queries)

        self._seed(20, cohort=53)
        with CaptureQueriesContext(connection) as twenty:
            twenty_rows = get_cohort_counselors_training_progress(53)
        self.assertEqual(len(twenty_rows), 20)
        twenty_count = len(twenty.captured_queries)

        self.assertEqual(one_count, five_count)
        self.assertEqual(five_count, twenty_count)
        self.assertLessEqual(twenty_count, 12)


class AdminTrainingProgressDetailTests(TestCase):
    def setUp(self):
        self.admin = _user("admin-detail@example.com", "관리자", UserRole.ADMIN)
        self.counselor = _counselor("상세상담", 6)
        self.http = HttpClient()
        self.http.force_login(self.admin)
        self.url = reverse(
            "admin_panel:counselor_training_progress_detail",
            kwargs={"counselor_id": self.counselor.pk},
        )

    def test_detail_shows_progress_and_actions(self):
        case = _case(self.counselor, _client_user("상세내담"), "CASE-DETAIL")
        _intake(case)
        response = self.http.get(self.url, {"cohort": "6"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "상세상담 상담사 수련현황")
        self.assertContains(response, "6기")
        self.assertContains(response, "접수면접")
        self.assertContains(response, "상담자 경험")
        self.assertContains(response, "종결기록지")
        self.assertContains(response, "수퍼비전")
        self.assertContains(response, "사례보고서")
        self.assertNotContains(response, "공개사례발표회 참여")
        self.assertContains(response, "수련시간")
        self.assertContains(response, "← 수련현황 목록")
        list_url = reverse("admin_panel:training_progress")
        self.assertContains(response, f"{list_url}?cohort=6")
        self.assertContains(
            response,
            reverse(
                "admin_panel:counselor_training_certificate",
                kwargs={"counselor_id": self.counselor.pk},
            ),
        )
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(response.context["training_progress"], progress)

    def test_case_report_detail_matches_list_meaning(self):
        cases = [
            _case(self.counselor, _client_user(f"보고서내담{i}"), f"CASE-REP-{i}")
            for i in range(4)
        ]
        _presentation(self.counselor, "미연결글", case=None, cohort=6)
        listing = self.http.get(
            reverse("admin_panel:training_progress"), {"cohort": "6"}
        )
        detail = self.http.get(self.url, {"cohort": "6"})
        detail_body = detail.content.decode()
        report_section = detail_body[
            detail_body.index("사례보고서") : detail_body.index("수련시간")
        ]
        self.assertContains(listing, "1사례 완료")
        self.assertNotContains(listing, "1/4사례")
        self.assertNotContains(listing, "공개사례발표")
        self.assertIn("1사례 완료", report_section)
        self.assertNotIn("4건 중 1건 제출", report_section)
        self.assertNotIn("공개사례발표회 참여", detail_body)
        self.assertIn("사례발표 게시글 제출 기준", report_section)
        self.assertNotIn("담당 사례에 연결된 사례발표 게시글 기준", report_section)
        individual = get_counselor_training_progress(self.counselor)
        batch_rows = get_cohort_counselors_training_progress(6)
        batch = next(row["progress"] for row in batch_rows if row["counselor"].pk == self.counselor.pk)
        self.assertEqual(individual["case_report_required_count"], 4)
        self.assertEqual(individual["case_report_submitted_count"], 1)
        self.assertEqual(batch["case_report_submitted_count"], 1)
        self.assertEqual(detail.context["training_progress"], individual)

        _presentation(self.counselor, "연결1", case=cases[0], cohort=6)
        _presentation(self.counselor, "연결1중복", case=cases[0], cohort=6)
        _presentation(self.counselor, "연결2", case=cases[1], cohort=6)
        listing = self.http.get(
            reverse("admin_panel:training_progress"), {"cohort": "6"}
        )
        detail = self.http.get(self.url, {"cohort": "6"})
        detail_body = detail.content.decode()
        report_section = detail_body[
            detail_body.index("사례보고서") : detail_body.index("수련시간")
        ]
        # linked distinct 2 + legacy null 1 = 3
        self.assertContains(listing, "3사례 완료")
        self.assertNotContains(listing, "3/4사례")
        self.assertIn("3사례 완료", report_section)
        self.assertNotIn("4건 중 3건 제출", report_section)
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["case_report_required_count"], 4)
        self.assertEqual(progress["case_report_submitted_count"], 3)

    def test_missing_counselor_returns_404(self):
        import uuid

        response = self.http.get(
            reverse(
                "admin_panel:counselor_training_progress_detail",
                kwargs={"counselor_id": uuid.uuid4()},
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_non_counselor_user_returns_404(self):
        client = _client_user("상세내담자계정")
        response = self.http.get(
            reverse(
                "admin_panel:counselor_training_progress_detail",
                kwargs={"counselor_id": client.pk},
            )
        )
        self.assertEqual(response.status_code, 404)
