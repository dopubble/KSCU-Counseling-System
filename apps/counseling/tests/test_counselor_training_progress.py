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
    TRAINING_CREDIT_MINUTES_PER_SESSION,
    format_training_minutes,
    get_cohort_counselors_training_progress,
    get_counselor_training_progress,
)
from apps.documents.models import ConsentDocType, ConsentDocument
from apps.scheduling.constants import (
    DEFAULT_APPOINTMENT_DURATION_MINUTES,
    DEFAULT_ZOOM_HOST_BUFFER_MINUTES,
)
from apps.scheduling.models import Appointment
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


def _supervision_sessions(cohort, status, count, *, start=1):
    return [
        SupervisionSession.objects.create(
            cohort=cohort,
            session_number=start + offset,
            scheduled_at=timezone.now(),
            status=status,
        )
        for offset in range(count)
    ]


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
    counseling_method=CounselingMethod.IN_PERSON,
):
    application = CounselingApplication.objects.create(
        client=client,
        counseling_types=["진로상담"],
        reason="test",
        counseling_method=counseling_method,
        status=ApplicationStatus.IN_PROGRESS,
    )
    return Case.objects.create(
        application=application,
        client=client,
        counselor=counselor,
        case_number=case_number,
        status=status,
        termination_type=termination_type,
        counseling_method=counseling_method,
        total_sessions=total_sessions,
        remaining_sessions=remaining_sessions,
        closed_at=timezone.now() if status == CaseStatus.CLOSED else None,
    )


def _submit_intake(
    case,
    *,
    doc_type=ConsentDocType.INTAKE,
    filename="consents/intake.pdf",
    method=None,
):
    doc = ConsentDocument.objects.create(
        client=case.client,
        application=case.application,
        doc_type=doc_type,
        file=filename,
        uploaded_by=case.counselor,
    )
    if doc_type == ConsentDocType.INTAKE and method is not None:
        Case.objects.filter(pk=case.pk).update(intake_counseling_method=method)
        case.intake_counseling_method = method
    return doc


def _create_journal(case, session_number, *, is_draft=False, counseling_method=None):
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
        self.assertEqual(format_training_minutes(60), "1시간")
        self.assertEqual(format_training_minutes(100), "1시간 40분")
        self.assertEqual(format_training_minutes(120), "2시간")
        self.assertEqual(format_training_minutes(150), "2시간 30분")
        self.assertEqual(format_training_minutes(180), "3시간")
        self.assertEqual(format_training_minutes(600), "10시간")
        self.assertEqual(format_training_minutes(1000), "16시간 40분")
        self.assertEqual(format_training_minutes(1200), "20시간")


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
        self.assertEqual(progress["supervision_total_count"], 0)
        self.assertEqual(progress["supervision_completed_count"], 0)
        self.assertEqual(progress["supervision_required_count"], 0)
        self.assertEqual(progress["supervision_attended_count"], 0)
        self.assertEqual(progress["supervision_attendance_percent"], 0)
        self.assertEqual(progress["case_report_required_count"], 0)
        self.assertEqual(progress["case_report_submitted_count"], 0)
        self.assertEqual(progress["public_presentation_participation_count"], 0)

    def test_missing_counselor_does_not_query(self):
        with self.assertNumQueries(0):
            progress = get_counselor_training_progress(None)
        self.assertEqual(progress["counseling_progress_percent"], 0)

    def test_intake_counts_submitted_intake_forms(self):
        first = _create_case(self.counselor, _create_client("초기1"), "CASE-TRAIN-I1")
        second = _create_case(self.counselor, _create_client("초기2"), "CASE-TRAIN-I2")
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["intake_required_count"], 2)
        self.assertEqual(progress["intake_completed_count"], 0)

        _submit_intake(first)
        InitialCounselingRecord.objects.create(
            case=second, counselor=self.counselor, is_draft=False
        )
        empty_intake = ConsentDocument.objects.create(
            client=second.client,
            application=second.application,
            doc_type=ConsentDocType.INTAKE,
            file="",
            uploaded_by=self.counselor,
        )
        _submit_intake(first, doc_type=ConsentDocType.PRIVACY, filename="consents/privacy.pdf")
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["intake_required_count"], 2)
        self.assertEqual(progress["intake_completed_count"], 1)
        self.assertEqual(progress["intake_required_minutes"], 120)
        self.assertEqual(progress["intake_completed_minutes"], 60)
        self.assertEqual(progress["intake_required_label"], "2시간")
        self.assertEqual(progress["intake_completed_label"], "1시간")

        empty_intake.file = "consents/second-intake.pdf"
        empty_intake.save(update_fields=["file"])
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["intake_completed_count"], 2)
        self.assertEqual(progress["intake_completed_minutes"], 120)
        self.assertEqual(progress["intake_completed_label"], "2시간")
        self.assertEqual(TRAINING_CREDIT_MINUTES_PER_SESSION, 60)
        self.assertEqual(DEFAULT_APPOINTMENT_DURATION_MINUTES, 50)
        self.assertEqual(
            Appointment._meta.get_field("duration_minutes").default,
            DEFAULT_APPOINTMENT_DURATION_MINUTES,
        )
        self.assertEqual(DEFAULT_ZOOM_HOST_BUFFER_MINUTES, 30)

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
        self.assertEqual(progress["counseling_assigned_minutes"], 1200)
        self.assertEqual(progress["counseling_completed_minutes"], 240)
        self.assertEqual(progress["counseling_assigned_label"], "20시간")
        self.assertEqual(progress["counseling_completed_label"], "4시간")

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
        _submit_intake(other_case)

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["intake_required_count"], 0)
        self.assertEqual(progress["counseling_assigned_count"], 0)
        self.assertEqual(progress["journal_completed_count"], 0)

    def test_termination_required_counts_every_assigned_case(self):
        active = _create_case(
            self.counselor,
            _create_client("진행중"),
            "CASE-TRAIN-OPEN",
            total_sessions=10,
            remaining_sessions=7,
        )
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["termination_required_count"], 1)
        self.assertEqual(progress["termination_completed_count"], 0)

        ready = _create_case(
            self.counselor,
            _create_client("종결준비"),
            "CASE-TRAIN-READY",
            remaining_sessions=0,
        )
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["termination_required_count"], 2)
        self.assertEqual(progress["termination_completed_count"], 0)

        TerminationCounselingRecord.objects.create(
            case=ready, counselor=self.counselor, is_draft=False
        )
        TerminationCounselingRecord.objects.create(
            case=active, counselor=self.counselor, is_draft=True
        )
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["termination_required_count"], 2)
        self.assertEqual(progress["termination_completed_count"], 1)

        _create_case(
            self.counselor,
            _create_client("정상"),
            "CASE-TRAIN-NORMAL",
            status=CaseStatus.CLOSED,
            termination_type=CaseTerminationType.NORMAL,
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
        other_case = _create_case(
            self.other,
            _create_client("타인종결"),
            "CASE-TRAIN-TERM-OTHER",
            status=CaseStatus.CLOSED,
            termination_type=CaseTerminationType.NORMAL,
            remaining_sessions=0,
        )
        TerminationCounselingRecord.objects.create(
            case=other_case, counselor=self.other, is_draft=False
        )

        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["termination_required_count"], 4)
        self.assertEqual(progress["termination_completed_count"], 1)
        other_progress = get_counselor_training_progress(self.other)
        self.assertEqual(other_progress["termination_required_count"], 1)
        self.assertEqual(other_progress["termination_completed_count"], 1)

    def test_supervision_total_includes_scheduled_but_attendance_rate_uses_completed_sessions(self):
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

        self.assertEqual(progress["supervision_total_count"], 3)
        self.assertEqual(progress["supervision_completed_count"], 2)
        self.assertEqual(progress["supervision_required_count"], 2)
        self.assertEqual(progress["supervision_attended_count"], 1)
        self.assertEqual(progress["supervision_attendance_percent"], 50)

    def test_registered_total_counts_scheduled_sessions_and_rate_uses_completed(self):
        profile = self.counselor.counselor_profile
        completed = _supervision_sessions(1, SupervisionSessionStatus.COMPLETED, 8)
        _supervision_sessions(1, SupervisionSessionStatus.SCHEDULED, 1, start=9)
        for session in completed[:7]:
            SupervisionAttendance.objects.create(
                session=session,
                counselor=profile,
                status=SupervisionAttendanceStatus.PRESENT,
            )

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["supervision_total_count"], 9)
        self.assertEqual(progress["supervision_completed_count"], 8)
        self.assertEqual(progress["supervision_attended_count"], 7)
        self.assertEqual(progress["supervision_attendance_percent"], 88)

    def test_cancelled_session_is_left_out_of_the_registered_total(self):
        _supervision_sessions(1, SupervisionSessionStatus.COMPLETED, 8)
        _supervision_sessions(1, SupervisionSessionStatus.SCHEDULED, 1, start=9)
        cancelled = _supervision_sessions(1, SupervisionSessionStatus.CANCELLED, 1, start=10)
        SupervisionAttendance.objects.create(
            session=cancelled[0],
            counselor=self.counselor.counselor_profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["supervision_total_count"], 9)
        self.assertEqual(progress["supervision_completed_count"], 8)
        self.assertEqual(progress["supervision_attended_count"], 0)
        self.assertEqual(progress["supervision_attendance_percent"], 0)

    def test_only_scheduled_sessions_show_zero_attendance_rate(self):
        _supervision_sessions(1, SupervisionSessionStatus.SCHEDULED, 9)

        progress = get_counselor_training_progress(self.counselor)

        self.assertEqual(progress["supervision_total_count"], 9)
        self.assertEqual(progress["supervision_completed_count"], 0)
        self.assertEqual(progress["supervision_attended_count"], 0)
        self.assertEqual(progress["supervision_attendance_percent"], 0)

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
        with self.assertNumQueries(8):
            get_counselor_training_progress(self.counselor)

        for index in range(3):
            extra = _create_case(
                self.counselor,
                _create_client(f"추가내담{index}"),
                f"CASE-TRAIN-Q{index}",
            )
            _create_journal(extra, 1)

        with self.assertNumQueries(8):
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

        # linked 2 + null 1 = 3이지만 required=2이므로 cap → 2
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
        with self.assertNumQueries(8):
            counted = get_counselor_training_progress(self.counselor)
        self.assertEqual(counted["case_report_submitted_count"], 2)

    def test_legacy_null_posts_count_toward_case_report(self):
        for index in range(4):
            _create_case(
                self.counselor,
                _create_client(f"레거시내담{index}"),
                f"CASE-LEG-{index}",
            )

        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["case_report_required_count"], 4)
        self.assertEqual(progress["case_report_submitted_count"], 0)

        _presentation_post(self.counselor, "레거시1")
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["case_report_submitted_count"], 1)

        _presentation_post(self.counselor, "레거시2")
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["case_report_submitted_count"], 2)

    def test_case_report_combines_linked_and_legacy_with_cap(self):
        cases = [
            _create_case(
                self.counselor,
                _create_client(f"합산내담{index}"),
                f"CASE-MIX-{index}",
            )
            for index in range(4)
        ]
        _presentation_post(self.counselor, "연결A", case=cases[0])
        _presentation_post(self.counselor, "연결A중복", case=cases[0])
        _presentation_post(self.counselor, "연결B", case=cases[1])
        _presentation_post(self.counselor, "레거시합산")

        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["case_report_required_count"], 4)
        self.assertEqual(progress["case_report_submitted_count"], 3)

        _presentation_post(self.counselor, "레거시초과1")
        _presentation_post(self.counselor, "레거시초과2")
        capped = get_counselor_training_progress(self.counselor)
        self.assertEqual(capped["case_report_submitted_count"], 4)

    def test_case_report_submitted_zero_when_required_zero(self):
        _presentation_post(self.counselor, "사례없음레거시")
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["case_report_required_count"], 0)
        self.assertEqual(progress["case_report_submitted_count"], 0)

    def test_training_minutes_split_by_session_method_snapshots(self):
        case = _create_case(
            self.counselor,
            _create_client("혼합내담"),
            "CASE-MIX-1",
            counseling_method=CounselingMethod.IN_PERSON,
        )
        _submit_intake(case, method=CounselingMethod.IN_PERSON)
        journal_ip = _create_journal(
            case, 1, counseling_method=CounselingMethod.IN_PERSON
        )
        Case.objects.filter(pk=case.pk).update(
            counseling_method=CounselingMethod.REMOTE
        )
        case.refresh_from_db()
        journal_ip.refresh_from_db()
        self.assertEqual(journal_ip.counseling_method, CounselingMethod.IN_PERSON)
        _create_journal(case, 2, counseling_method=CounselingMethod.REMOTE)
        legacy = _create_journal(case, 3, counseling_method=None)
        self.assertIsNone(legacy.counseling_method)

        ip_session = SupervisionSession.objects.create(
            cohort=1,
            session_number=1,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.COMPLETED,
            duration_minutes=60,
            counseling_method=CounselingMethod.IN_PERSON,
        )
        remote_session = SupervisionSession.objects.create(
            cohort=1,
            session_number=2,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.COMPLETED,
            duration_minutes=90,
            counseling_method=CounselingMethod.REMOTE,
        )
        legacy_session = SupervisionSession.objects.create(
            cohort=1,
            session_number=3,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.COMPLETED,
            duration_minutes=60,
            counseling_method=None,
        )
        cancelled = SupervisionSession.objects.create(
            cohort=1,
            session_number=4,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.CANCELLED,
            duration_minutes=60,
            counseling_method=CounselingMethod.IN_PERSON,
        )
        scheduled = SupervisionSession.objects.create(
            cohort=1,
            session_number=5,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.SCHEDULED,
            duration_minutes=60,
            counseling_method=CounselingMethod.REMOTE,
        )
        absent_session = SupervisionSession.objects.create(
            cohort=1,
            session_number=6,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.COMPLETED,
            duration_minutes=60,
            counseling_method=CounselingMethod.IN_PERSON,
        )
        profile = self.counselor.counselor_profile
        for session in (ip_session, remote_session, legacy_session):
            SupervisionAttendance.objects.create(
                session=session,
                counselor=profile,
                status=SupervisionAttendanceStatus.PRESENT,
            )
        SupervisionAttendance.objects.create(
            session=cancelled,
            counselor=profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )
        SupervisionAttendance.objects.create(
            session=scheduled,
            counselor=profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )
        SupervisionAttendance.objects.create(
            session=absent_session,
            counselor=profile,
            status=SupervisionAttendanceStatus.ABSENT,
        )

        progress = get_counselor_training_progress(self.counselor)
        # intake 1h IP; journals 1h IP + 1h RM + legacy NULL→Case REMOTE 1h;
        # supervision 1h IP + 1.5h RM + legacy NULL→REMOTE 1h
        self.assertEqual(progress["training_in_person_minutes"], 180)
        self.assertEqual(progress["training_remote_minutes"], 270)
        self.assertEqual(progress["training_unknown_minutes"], 0)
        self.assertEqual(progress["training_completed_minutes"], 450)
        self.assertEqual(
            progress["training_in_person_minutes"]
            + progress["training_remote_minutes"]
            + progress["training_unknown_minutes"],
            progress["training_completed_minutes"],
        )
        self.assertEqual(
            progress["training_method_summary_label"],
            "대면 3시간 / 비대면 4시간 30분",
        )
        batch = get_cohort_counselors_training_progress(1)
        batch_progress = next(
            row["progress"] for row in batch if row["counselor"].pk == self.counselor.pk
        )
        for key in (
            "training_in_person_minutes",
            "training_remote_minutes",
            "training_unknown_minutes",
            "training_completed_minutes",
            "training_method_summary_label",
        ):
            self.assertEqual(batch_progress[key], progress[key], key)

    def test_legacy_null_journals_fall_back_to_case_method_by_case(self):
        """운영 회귀: snapshot NULL이어도 Case method가 있으면 방식 미확인이 아니다."""
        in_person = _create_case(
            self.counselor,
            _create_client("레거시대면"),
            "CASE-LEG-IP",
            counseling_method=CounselingMethod.IN_PERSON,
        )
        remote = _create_case(
            self.counselor,
            _create_client("레거시비대면"),
            "CASE-LEG-RM",
            counseling_method=CounselingMethod.REMOTE,
        )
        for number in range(1, 5):
            _create_journal(in_person, number, counseling_method=None)
        for number in range(1, 7):
            _create_journal(remote, number, counseling_method=None)

        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["training_in_person_minutes"], 240)
        self.assertEqual(progress["training_remote_minutes"], 360)
        self.assertEqual(progress["training_unknown_minutes"], 0)
        self.assertEqual(progress["training_completed_minutes"], 600)
        self.assertEqual(
            progress["training_method_summary_label"],
            "대면 4시간 / 비대면 6시간",
        )
        batch = get_cohort_counselors_training_progress(1)
        batch_progress = next(
            row["progress"] for row in batch if row["counselor"].pk == self.counselor.pk
        )
        self.assertEqual(batch_progress["training_in_person_minutes"], 240)
        self.assertEqual(batch_progress["training_remote_minutes"], 360)
        self.assertEqual(batch_progress["training_unknown_minutes"], 0)

    def test_journal_snapshot_beats_case_fallback(self):
        case = _create_case(
            self.counselor,
            _create_client("스냅우선"),
            "CASE-SNAP-1",
            counseling_method=CounselingMethod.REMOTE,
        )
        _create_journal(case, 1, counseling_method=CounselingMethod.IN_PERSON)
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["counseling_in_person_minutes"], 60)
        self.assertEqual(progress["counseling_remote_minutes"], 0)
        self.assertEqual(progress["training_unknown_minutes"], 0)

    def test_intake_falls_back_to_case_method_when_snapshot_null(self):
        case = _create_case(
            self.counselor,
            _create_client("접수폴백"),
            "CASE-INT-FB",
            counseling_method=CounselingMethod.IN_PERSON,
        )
        _submit_intake(case)  # intake_counseling_method stays NULL
        self.assertIsNone(case.intake_counseling_method)
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["intake_in_person_minutes"], 60)
        self.assertEqual(progress["intake_remote_minutes"], 0)
        self.assertEqual(progress["intake_unknown_minutes"], 0)

        Case.objects.filter(pk=case.pk).update(
            intake_counseling_method=CounselingMethod.REMOTE,
            counseling_method=CounselingMethod.IN_PERSON,
        )
        snapped = get_counselor_training_progress(self.counselor)
        self.assertEqual(snapped["intake_remote_minutes"], 60)
        self.assertEqual(snapped["intake_in_person_minutes"], 0)

    def test_truly_unknown_when_journal_and_case_method_missing(self):
        case = _create_case(
            self.counselor,
            _create_client("진짜미확인"),
            "CASE-UNK-1",
            counseling_method=CounselingMethod.IN_PERSON,
        )
        Case.objects.filter(pk=case.pk).update(counseling_method="")
        _create_journal(case, 1, counseling_method=None)
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(progress["training_unknown_minutes"], 60)
        self.assertEqual(progress["training_in_person_minutes"], 0)
        self.assertEqual(progress["training_remote_minutes"], 0)
        self.assertEqual(
            progress["training_method_summary_label"],
            "방식 미확인 1시간",
        )

    def test_legacy_supervision_null_falls_back_to_remote_policy(self):
        in_person = _create_case(
            self.counselor,
            _create_client("수퍼폴백대면"),
            "CASE-SUP-IP",
            counseling_method=CounselingMethod.IN_PERSON,
        )
        remote = _create_case(
            self.counselor,
            _create_client("수퍼폴백비대면"),
            "CASE-SUP-RM",
            counseling_method=CounselingMethod.REMOTE,
        )
        for number in range(1, 5):
            _create_journal(in_person, number, counseling_method=None)
        for number in range(1, 7):
            _create_journal(remote, number, counseling_method=None)

        profile = self.counselor.counselor_profile
        legacy = SupervisionSession.objects.create(
            cohort=1,
            session_number=1,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.COMPLETED,
            duration_minutes=420,
            counseling_method=None,
        )
        new_ip = SupervisionSession.objects.create(
            cohort=1,
            session_number=2,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.COMPLETED,
            duration_minutes=60,
            counseling_method=CounselingMethod.IN_PERSON,
        )
        new_rm = SupervisionSession.objects.create(
            cohort=1,
            session_number=3,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.COMPLETED,
            duration_minutes=30,
            counseling_method=CounselingMethod.REMOTE,
        )
        absent = SupervisionSession.objects.create(
            cohort=1,
            session_number=4,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.COMPLETED,
            duration_minutes=60,
            counseling_method=None,
        )
        cancelled = SupervisionSession.objects.create(
            cohort=1,
            session_number=5,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.CANCELLED,
            duration_minutes=60,
            counseling_method=None,
        )
        scheduled = SupervisionSession.objects.create(
            cohort=1,
            session_number=6,
            scheduled_at=timezone.now(),
            status=SupervisionSessionStatus.SCHEDULED,
            duration_minutes=60,
            counseling_method=None,
        )
        for session in (legacy, new_ip, new_rm):
            SupervisionAttendance.objects.create(
                session=session,
                counselor=profile,
                status=SupervisionAttendanceStatus.PRESENT,
            )
        SupervisionAttendance.objects.create(
            session=absent,
            counselor=profile,
            status=SupervisionAttendanceStatus.ABSENT,
        )
        SupervisionAttendance.objects.create(
            session=cancelled,
            counselor=profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )
        SupervisionAttendance.objects.create(
            session=scheduled,
            counselor=profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )
        legacy.refresh_from_db()
        self.assertIsNone(legacy.counseling_method)

        progress = get_counselor_training_progress(self.counselor)
        # counseling 4h IP + 6h RM; supervision legacy 7h RM + 1h IP + 0.5h RM
        self.assertEqual(progress["training_in_person_minutes"], 300)
        self.assertEqual(progress["training_remote_minutes"], 810)
        self.assertEqual(progress["training_unknown_minutes"], 0)
        self.assertEqual(progress["training_completed_minutes"], 1110)
        self.assertEqual(
            progress["training_method_summary_label"],
            "대면 5시간 / 비대면 13시간 30분",
        )
        batch = get_cohort_counselors_training_progress(1)
        batch_progress = next(
            row["progress"] for row in batch if row["counselor"].pk == self.counselor.pk
        )
        self.assertEqual(batch_progress["training_in_person_minutes"], 300)
        self.assertEqual(batch_progress["training_remote_minutes"], 810)
        self.assertEqual(batch_progress["training_unknown_minutes"], 0)


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
        self.assertContains(response, "수련시간")
        self.assertContains(response, "총 0분")
        self.assertNotContains(response, "공개사례발표회 참여")
        self.assertNotContains(response, "댓글 참여 기준")
        self.assertContains(response, "총 0건(총 0분) / 0건 완료(0분)")
        self.assertNotContains(response, "총 0건 · 0분")
        self.assertNotContains(response, "0건 완료 · 0분")
        self.assertNotContains(response, "총 0회 / 0회 완료")
        self.assertContains(response, "총 0분 / 0분 완료")
        self.assertContains(response, "총 0회 / 0회 출석")
        self.assertNotContains(response, "완료 0회 중")
        self.assertNotContains(response, "총 0회 / 중 0회 출석")
        self.assertNotContains(response, "0회 참여")
        self.assertContains(response, "총 0건 / 0건 제출", count=1)
        self.assertContains(response, "0사례 완료")
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
        _submit_intake(case)
        progress = get_counselor_training_progress(self.counselor)
        response = self.http.get(self.url)
        body = response.content.decode()

        self.assertEqual(progress["counseling_assigned_count"], 20)
        self.assertEqual(progress["journal_completed_count"], 3)
        self.assertEqual(progress["counseling_progress_percent"], 15)
        self.assertEqual(progress["counseling_assigned_minutes"], 1200)
        self.assertEqual(progress["counseling_completed_minutes"], 180)
        self.assertEqual(response.context["training_progress"], progress)
        self.assertIn("총 1건(총 1시간) / 1건 완료(1시간)", body)
        self.assertNotIn("총 1건 · 1시간", body)
        self.assertNotIn("1건 완료 · 1시간", body)
        self.assertNotIn("총 20회 / 3회 완료", body)
        self.assertIn("15%", body)
        self.assertIn("총 20시간 / 3시간 완료", body)
        self.assertEqual(progress["termination_required_count"], 1)
        self.assertEqual(progress["termination_completed_count"], 0)
        self.assertEqual(progress["case_report_required_count"], 1)
        self.assertEqual(progress["case_report_submitted_count"], 0)
        self.assertIn("총 1건 / 0건 제출", body)
        self.assertIn("0사례 완료", body)
        self.assertNotIn("공개사례발표회 참여", body)
        self.assertNotIn("완료 회기", body)
        self.assertNotIn("작성완료", body)
        self.assertNotIn("게시글이 사례와 연결되어 있지 않아", body)

    def test_dashboard_supervision_text_separates_total_and_completed(self):
        profile = self.counselor.counselor_profile
        completed = _supervision_sessions(1, SupervisionSessionStatus.COMPLETED, 8)
        _supervision_sessions(1, SupervisionSessionStatus.SCHEDULED, 1, start=9)
        for session in completed[:7]:
            SupervisionAttendance.objects.create(
                session=session,
                counselor=profile,
                status=SupervisionAttendanceStatus.PRESENT,
            )

        response = self.http.get(self.url)

        self.assertContains(response, "총 9회 / 7회 출석")
        self.assertNotContains(response, "완료 8회 중")
        self.assertNotContains(response, "총 9회 / 중 7회 출석")
        self.assertContains(response, "88%")

    def test_dashboard_shows_linked_case_report_counts(self):
        first = _create_case(self.counselor, _create_client("화면사례1"), "CASE-UI-1")
        _create_case(self.counselor, _create_client("화면사례2"), "CASE-UI-2")
        _presentation_post(self.counselor, "화면보고서", case=first)
        _presentation_post(self.counselor, "화면보고서2", case=first)
        _presentation_post(self.counselor, "화면보고서3", case=first)

        response = self.http.get(self.url)
        self.assertEqual(response.status_code, 200)
        progress = response.context["training_progress"]
        self.assertEqual(progress["case_report_required_count"], 2)
        self.assertEqual(progress["case_report_submitted_count"], 1)
        self.assertContains(response, "1사례 완료")
        self.assertNotContains(response, "총 2건 / 1건 제출")
        self.assertNotContains(response, "1/2사례")
        self.assertNotContains(
            response,
            "게시글이 사례와 연결되어 있지 않아 제출 수를 집계하지 않습니다.",
        )

    def test_dashboard_counts_every_case_toward_termination_records(self):
        ready = _create_case(
            self.counselor,
            _create_client("화면종결준비"),
            "CASE-UI-READY",
            remaining_sessions=0,
        )
        _create_case(
            self.counselor,
            _create_client("화면진행"),
            "CASE-UI-OPEN",
            total_sessions=10,
            remaining_sessions=7,
        )
        TerminationCounselingRecord.objects.create(
            case=ready, counselor=self.counselor, is_draft=False
        )

        response = self.http.get(self.url)
        progress = response.context["training_progress"]

        self.assertEqual(progress["termination_required_count"], 2)
        self.assertEqual(progress["termination_completed_count"], 1)
        self.assertContains(response, "총 2건 / 1건 제출")

    def test_dashboard_counts_submitted_intake_forms(self):
        submitted = _create_case(
            self.counselor, _create_client("면접제출"), "CASE-2026-45"
        )
        _create_case(self.counselor, _create_client("면접없음"), "CASE-UI-NO-INTAKE")
        _submit_intake(submitted)
        InitialCounselingRecord.objects.create(
            case=submitted, counselor=self.counselor, is_draft=True
        )

        response = self.http.get(self.url)
        progress = response.context["training_progress"]

        self.assertEqual(progress["intake_required_count"], 2)
        self.assertEqual(progress["intake_completed_count"], 1)
        self.assertEqual(progress["intake_required_label"], "2시간")
        self.assertEqual(progress["intake_completed_label"], "1시간")
        self.assertContains(response, "총 2건(총 2시간) / 1건 완료(1시간)")
        self.assertNotContains(response, "총 2건 · 2시간")
        self.assertNotContains(response, "1건 완료 · 1시간")
