"""상담사 대시보드 수련 현황."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CasePresentationComment,
    CasePresentationPost,
    CaseStatus,
    CaseTerminationType,
    CounselingApplication,
    CounselingMethod,
    SupervisionAttendance,
    SupervisionAttendanceStatus,
    SupervisionSession,
    SupervisionSessionStatus,
)
from apps.counseling.training_progress import (
    format_training_minutes,
    get_counselor_training_progress,
)
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.sessions_app.models import (
    CounselingJournal,
    InitialCounselingRecord,
    TerminationCounselingRecord,
)


def _create_client(name: str) -> User:
    return User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.CLIENT,
        status=UserStatus.ACTIVE,
    )


def _create_counselor(name: str, *, cohort=1) -> User:
    user = User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.COUNSELOR,
        status=UserStatus.ACTIVE,
    )
    CounselorProfile.objects.update_or_create(
        user=user,
        defaults={"cohort": cohort, "is_approved": True},
    )
    return user


def _create_case(
    counselor,
    client,
    case_number,
    *,
    status=CaseStatus.ACTIVE,
    termination_type="",
    total_sessions=10,
    remaining_sessions=10,
):
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
        status=status,
        termination_type=termination_type,
        counseling_method=CounselingMethod.IN_PERSON,
        total_sessions=total_sessions,
        remaining_sessions=remaining_sessions,
        closed_at=timezone.now() if status == CaseStatus.CLOSED else None,
    )


def _create_journal(case, session_number, *, is_draft=False):
    return CounselingJournal.objects.create(
        case=case,
        counselor=case.counselor,
        session_number=session_number,
        is_draft=is_draft,
        subjective="S",
        objective="O",
        assessment="A",
        plan="P",
    )


def _presentation_post(author, title, *, case=None):
    return CasePresentationPost.objects.create(
        cohort=1,
        author=author,
        case=case,
        title=title,
        file=SimpleUploadedFile(f"{title}.pdf", b"%PDF-1.4"),
    )


class FormatTrainingMinutesTests(TestCase):
    def test_formats_hours_and_minutes(self):
        self.assertEqual(format_training_minutes(0), "0분")
        self.assertEqual(format_training_minutes(50), "50분")
        self.assertEqual(format_training_minutes(100), "1시간 40분")
        self.assertEqual(format_training_minutes(150), "2시간 30분")
        self.assertEqual(format_training_minutes(600), "10시간")
        self.assertEqual(format_training_minutes(1000), "16시간 40분")


class CounselorTrainingProgressTests(TestCase):
    def setUp(self):
        self.counselor = _create_counselor("수련상담사")
        self.other = _create_counselor("다른상담사", cohort=2)

    def test_empty_counselor_is_zero_without_division(self):
        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["intake_required_count"], 0)
        self.assertEqual(progress["intake_completed_count"], 0)
        self.assertEqual(progress["counseling_assigned_count"], 0)
        self.assertEqual(progress["journal_completed_count"], 0)
        self.assertEqual(progress["counseling_progress_percent"], 0)
        self.assertEqual(progress["termination_required_count"], 0)
        self.assertEqual(progress["supervision_required_count"], 0)
        self.assertEqual(progress["supervision_attendance_percent"], 0)
        self.assertEqual(progress["case_report_required_count"], 0)
        self.assertEqual(progress["case_report_submitted_count"], 0)
        self.assertEqual(progress["public_presentation_participation_count"], 0)

    def test_missing_counselor_does_not_query(self):
        with self.assertNumQueries(0):
            progress = get_counselor_training_progress(None)
        self.assertEqual(progress["counseling_progress_percent"], 0)

    def test_intake_counts_cases_and_completed_initial_records(self):
        first = _create_case(self.counselor, _create_client("초기1"), "CASE-TRAIN-I1")
        second = _create_case(self.counselor, _create_client("초기2"), "CASE-TRAIN-I2")
        InitialCounselingRecord.objects.create(
            case=first, counselor=self.counselor, is_draft=False
        )
        InitialCounselingRecord.objects.create(
            case=second, counselor=self.counselor, is_draft=True
        )

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["intake_required_count"], 2)
        self.assertEqual(progress["intake_completed_count"], 1)
        self.assertEqual(DEFAULT_APPOINTMENT_DURATION_MINUTES, 50)
        self.assertEqual(progress["intake_required_minutes"], 100)
        self.assertEqual(progress["intake_completed_minutes"], 50)
        self.assertEqual(progress["intake_required_label"], "1시간 40분")
        self.assertEqual(progress["intake_completed_label"], "50분")

    def test_counseling_experience_uses_assigned_sessions_including_early_closure(self):
        active = _create_case(
            self.counselor,
            _create_client("진행내담"),
            "CASE-TRAIN-A",
            total_sessions=10,
            remaining_sessions=7,
        )
        early = _create_case(
            self.counselor,
            _create_client("조기내담"),
            "CASE-TRAIN-E",
            status=CaseStatus.CLOSED,
            termination_type=CaseTerminationType.EARLY,
            total_sessions=10,
            remaining_sessions=6,
        )
        for number in range(1, 4):
            _create_journal(active, number)
        _create_journal(active, 4, is_draft=True)
        _create_journal(early, 1)

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["counseling_assigned_count"], 20)
        self.assertEqual(progress["journal_completed_count"], 4)
        self.assertEqual(progress["counseling_progress_percent"], 20)
        self.assertEqual(progress["counseling_assigned_minutes"], 1000)
        self.assertEqual(progress["counseling_completed_minutes"], 200)
        self.assertEqual(progress["counseling_assigned_label"], "16시간 40분")
        self.assertEqual(progress["counseling_completed_label"], "3시간 20분")

    def test_progress_bar_percent_does_not_exceed_100(self):
        case = _create_case(
            self.counselor,
            _create_client("초과내담"),
            "CASE-TRAIN-OVER",
            total_sessions=1,
            remaining_sessions=0,
        )
        _create_journal(case, 1)
        _create_journal(case, 2)

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["counseling_assigned_count"], 1)
        self.assertEqual(progress["journal_completed_count"], 2)
        self.assertEqual(progress["counseling_progress_percent"], 100)

    def test_other_counselor_records_are_excluded(self):
        other_case = _create_case(
            self.other, _create_client("다른내담"), "CASE-TRAIN-OTHER", total_sessions=8
        )
        _create_journal(other_case, 1)
        InitialCounselingRecord.objects.create(
            case=other_case, counselor=self.other, is_draft=False
        )

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["intake_required_count"], 0)
        self.assertEqual(progress["counseling_assigned_count"], 0)
        self.assertEqual(progress["journal_completed_count"], 0)

    def test_termination_targets_exclude_ordinary_active_cases(self):
        _create_case(
            self.counselor,
            _create_client("진행중"),
            "CASE-TRAIN-OPEN",
            total_sessions=10,
            remaining_sessions=7,
        )
        ready = _create_case(
            self.counselor,
            _create_client("종결준비"),
            "CASE-TRAIN-READY",
            remaining_sessions=0,
        )
        _create_case(
            self.counselor,
            _create_client("조기"),
            "CASE-TRAIN-EARLY-T",
            status=CaseStatus.CLOSED,
            termination_type=CaseTerminationType.EARLY,
            remaining_sessions=6,
        )
        finished = _create_case(
            self.counselor,
            _create_client("정상"),
            "CASE-TRAIN-NORMAL",
            status=CaseStatus.CLOSED,
            termination_type=CaseTerminationType.NORMAL,
            remaining_sessions=0,
        )
        TerminationCounselingRecord.objects.create(
            case=ready, counselor=self.counselor, is_draft=False
        )
        TerminationCounselingRecord.objects.create(
            case=finished, counselor=self.counselor, is_draft=True
        )

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["termination_required_count"], 3)
        self.assertEqual(progress["termination_completed_count"], 1)

    def test_supervision_attendance_uses_completed_sessions_only(self):
        profile = self.counselor.counselor_profile
        completed = SupervisionSession.objects.create(
            cohort=1,
            session_number=1,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.COMPLETED,
        )
        scheduled = SupervisionSession.objects.create(
            cohort=1,
            session_number=2,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.SCHEDULED,
        )
        cancelled = SupervisionSession.objects.create(
            cohort=1,
            session_number=3,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.CANCELLED,
        )
        second_completed = SupervisionSession.objects.create(
            cohort=1,
            session_number=4,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.COMPLETED,
        )
        SupervisionAttendance.objects.create(
            session=completed,
            counselor=profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )
        SupervisionAttendance.objects.create(
            session=second_completed,
            counselor=profile,
            status=SupervisionAttendanceStatus.ABSENT,
        )
        SupervisionAttendance.objects.create(
            session=scheduled,
            counselor=profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )
        SupervisionAttendance.objects.create(
            session=cancelled,
            counselor=profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["supervision_required_count"], 2)
        self.assertEqual(progress["supervision_attended_count"], 1)
        self.assertEqual(progress["supervision_attendance_percent"], 50)

    def test_public_presentation_counts_distinct_posts_not_repeat_comments(self):
        first = _presentation_post(self.counselor, "발표1")
        second = _presentation_post(self.counselor, "발표2")
        other_post = _presentation_post(self.other, "다른발표")
        CasePresentationComment.objects.create(post=first, author=self.counselor, content="1")
        CasePresentationComment.objects.create(post=first, author=self.counselor, content="2")
        CasePresentationComment.objects.create(post=second, author=self.counselor, content="3")
        CasePresentationComment.objects.create(post=other_post, author=self.other, content="4")

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["public_presentation_participation_count"], 2)

    def test_query_count_does_not_grow_with_more_cases(self):
        case = _create_case(self.counselor, _create_client("쿼리내담"), "CASE-TRAIN-Q")
        _create_journal(case, 1)
        with self.assertNumQueries(4):
            get_counselor_training_progress(self.counselor)

        for index in range(3):
            extra = _create_case(
                self.counselor,
                _create_client(f"추가내담{index}"),
                f"CASE-TRAIN-Q{index}",
            )
            _create_journal(extra, 1)

        with self.assertNumQueries(4):
            get_counselor_training_progress(self.counselor)

    def test_case_report_counts_distinct_owned_cases(self):
        first = _create_case(self.counselor, _create_client("사례1"), "CASE-001")
        second = _create_case(self.counselor, _create_client("사례2"), "CASE-002")

        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["case_report_required_count"], 2)
        self.assertEqual(progress["case_report_submitted_count"], 0)

        _presentation_post(self.counselor, "보고서1", case=first)
        _presentation_post(self.counselor, "보고서1중복", case=first)
        _presentation_post(self.counselor, "보고서1추가", case=first)
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(
            CasePresentationPost.objects.filter(author=self.counselor, case=first).count(),
            3,
        )
        self.assertEqual(progress["case_report_submitted_count"], 1)

        _presentation_post(self.counselor, "보고서2", case=second)
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["case_report_submitted_count"], 2)

        legacy = _presentation_post(self.counselor, "기존글")
        self.assertIsNone(legacy.case_id)
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["case_report_submitted_count"], 2)

        other_case = _create_case(self.other, _create_client("타인사례"), "CASE-OTHER")
        _presentation_post(self.other, "타인보고서", case=other_case)
        _presentation_post(self.other, "타인글이내사례", case=first)
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["case_report_required_count"], 2)
        self.assertEqual(progress["case_report_submitted_count"], 2)
        other_progress = get_counselor_training_progress(self.other)
        self.assertEqual(other_progress["case_report_required_count"], 1)
        self.assertEqual(other_progress["case_report_submitted_count"], 1)

        linked = _presentation_post(self.counselor, "연결후쿼리", case=None)
        self.assertIsNone(linked.case_id)
        with self.assertNumQueries(4):
            counted = get_counselor_training_progress(self.counselor)
        self.assertEqual(counted["case_report_submitted_count"], 2)


class CounselorTrainingProgressDashboardTests(TestCase):
    def setUp(self):
        self.counselor = _create_counselor("화면상담사")
        self.http = HttpClient()
        self.http.force_login(self.counselor)
        self.url = reverse("counselor:dashboard")

    def test_dashboard_renders_zero_training_areas(self):
        response = self.http.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "수련 현황")
        self.assertContains(response, "접수면접")
        self.assertContains(response, "상담자 경험")
        self.assertContains(response, "종결기록지")
        self.assertContains(response, "수퍼비전")
        self.assertContains(response, "사례보고서")
        self.assertContains(response, "공개사례발표회 참여")
        self.assertContains(response, "총 0건 · 0분")
        self.assertContains(response, "0건 완료 · 0분")
        self.assertContains(response, "총 0회 / 0회 완료")
        self.assertContains(response, "총 0분 / 0분 완료")
        self.assertContains(response, "총 0회 / 0회 출석")
        self.assertContains(response, "0회 참여")
        self.assertContains(response, "댓글 참여 기준")
        self.assertContains(response, "총 0건 / 0건 제출", count=2)
        self.assertNotContains(
            response,
            "게시글이 사례와 연결되어 있지 않아 제출 수를 집계하지 않습니다.",
        )
        self.assertContains(response, "사례발표 게시판")
        self.assertNotContains(response, "작성완료")
        self.assertNotContains(response, "완료 회기")
        self.assertNotContains(response, "미작성")
        self.assertNotContains(response, "작성중")
        self.assertIn("cases", response.context)
        self.assertIn("pending_appointments", response.context)

    def test_dashboard_text_matches_progress(self):
        case = _create_case(
            self.counselor,
            _create_client("화면내담"),
            "CASE-TRAIN-UI",
            total_sessions=20,
            remaining_sessions=17,
        )
        for number in range(1, 4):
            _create_journal(case, number)
        InitialCounselingRecord.objects.create(
            case=case, counselor=self.counselor, is_draft=False
        )
        progress = get_counselor_training_progress(self.counselor)
        response = self.http.get(self.url)
        body = response.content.decode()

        self.assertEqual(progress["counseling_assigned_count"], 20)
        self.assertEqual(progress["journal_completed_count"], 3)
        self.assertEqual(progress["counseling_progress_percent"], 15)
        self.assertEqual(response.context["training_progress"], progress)
        self.assertIn("총 1건 · 50분", body)
        self.assertIn("1건 완료 · 50분", body)
        self.assertIn("총 20회 / 3회 완료", body)
        self.assertIn("15%", body)
        self.assertIn("총 16시간 40분 / 2시간 30분 완료", body)
        self.assertIn("총 1건 / 0건 제출", body)
        self.assertNotIn("완료 회기", body)
        self.assertNotIn("작성완료", body)
        self.assertNotIn("게시글이 사례와 연결되어 있지 않아", body)

    def test_dashboard_shows_linked_case_report_counts(self):
        first = _create_case(self.counselor, _create_client("화면사례1"), "CASE-UI-1")
        _create_case(self.counselor, _create_client("화면사례2"), "CASE-UI-2")
        _presentation_post(self.counselor, "화면보고서", case=first)
        _presentation_post(self.counselor, "화면보고서2", case=first)
        _presentation_post(self.counselor, "화면보고서3", case=first)

        response = self.http.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "총 2건 / 1건 제출")
        self.assertNotContains(
            response,
            "게시글이 사례와 연결되어 있지 않아 제출 수를 집계하지 않습니다.",
        )
