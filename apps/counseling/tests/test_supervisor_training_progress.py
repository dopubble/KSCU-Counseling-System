"""수퍼바이저 담당 기수 수련현황 조회."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client as HttpClient, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CounselorProfile, SupervisorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
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
from apps.sessions_app.models import CounselingJournal


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


def _supervisor(email, name, cohorts):
    user = _user(email, name, UserRole.SUPERVISOR)
    SupervisorProfile.objects.update_or_create(
        user=user,
        defaults={"assigned_cohorts": cohorts},
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


def _sessions(cohort, status, count, *, start=1, duration_minutes=60):
    return [
        SupervisionSession.objects.create(
            cohort=cohort,
            session_number=start + offset,
            scheduled_at=timezone.now(),
            status=status,
            duration_minutes=duration_minutes,
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
    "supervision_attended_count",
    "case_report_submitted_count",
    "training_completed_minutes",
    "training_completed_label",
    "training_in_person_minutes",
    "training_remote_minutes",
    "training_unknown_minutes",
)


class SupervisorTrainingProgressAccessTests(TestCase):
    def setUp(self):
        self.supervisor = _supervisor("sv-tp@example.com", "수련수퍼", [1])
        self.other = _supervisor("sv-tp-other@example.com", "다른기수", [2])
        self.counselor_c1 = _counselor("1기상담", 1)
        self.counselor_c2 = _counselor("2기상담", 2)
        self.admin = _user("sv-tp-admin@example.com", "관리자", UserRole.ADMIN)
        self.plain = _counselor("일반상담", 1)
        self.list_url = reverse("supervisor:training_progress")
        self.detail_c1 = reverse(
            "supervisor:counselor_training_progress_detail",
            kwargs={"counselor_id": self.counselor_c1.pk},
        )
        self.detail_c2 = reverse(
            "supervisor:counselor_training_progress_detail",
            kwargs={"counselor_id": self.counselor_c2.pk},
        )
        self.cert_c1 = reverse(
            "supervisor:counselor_training_certificate",
            kwargs={"counselor_id": self.counselor_c1.pk},
        )
        self.cert_c2 = reverse(
            "supervisor:counselor_training_certificate",
            kwargs={"counselor_id": self.counselor_c2.pk},
        )
        self.http = HttpClient()

    def test_dashboard_shows_training_progress_card(self):
        self.http.force_login(self.supervisor)
        response = self.http.get(reverse("supervisor:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "기수별 수련현황")
        self.assertContains(response, self.list_url)
        self.assertContains(
            response,
            "담당 기수 상담사의 수련 진행상황과 수련시간을 확인합니다.",
        )

    def test_counselor_cannot_open_supervisor_training_urls(self):
        self.http.force_login(self.plain)
        self.assertEqual(self.http.get(self.list_url).status_code, 403)
        self.assertEqual(self.http.get(self.detail_c1).status_code, 403)
        self.assertEqual(self.http.get(self.cert_c1).status_code, 403)

    def test_anonymous_redirects_to_login(self):
        response = self.http.get(self.list_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login", response.url)

    def test_assigned_cohort_list_ok_other_cohort_forbidden(self):
        self.http.force_login(self.supervisor)
        ok = self.http.get(self.list_url, {"cohort": "1"})
        self.assertEqual(ok.status_code, 200)
        self.assertContains(ok, "1기상담")
        self.assertNotContains(ok, "2기상담")

        forbidden = self.http.get(self.list_url, {"cohort": "2"})
        self.assertEqual(forbidden.status_code, 403)

    def test_detail_and_certificate_forbid_other_cohort(self):
        self.http.force_login(self.supervisor)
        self.assertEqual(self.http.get(self.detail_c1).status_code, 200)
        self.assertEqual(self.http.get(self.detail_c2).status_code, 403)
        cert_ok = self.http.get(self.cert_c1)
        self.assertEqual(cert_ok.status_code, 200)
        self.assertEqual(cert_ok["Content-Type"], "application/pdf")
        self.assertEqual(self.http.get(self.cert_c2).status_code, 403)

    def test_no_assigned_cohorts_shows_message(self):
        empty = _supervisor("sv-tp-empty@example.com", "무배정", [])
        self.http.force_login(empty)
        response = self.http.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "배정된 기수가 없습니다.")


class SupervisorTrainingProgressListTests(TestCase):
    def setUp(self):
        self.supervisor = _supervisor("sv-list@example.com", "목록수퍼", [1, 3])
        self.counselor = _counselor("목록대상", 3)
        self.http = HttpClient()
        self.http.force_login(self.supervisor)

    def test_dropdown_only_assigned_and_defaults_to_latest_assigned(self):
        _counselor("1기대상", 1)
        _counselor("4기존재", 4)
        response = self.http.get(reverse("supervisor:training_progress"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_cohort"], 3)
        self.assertEqual(response.context["cohorts"], [3, 1])
        body = response.content.decode()
        self.assertIn(">3기<", body)
        self.assertIn(">1기<", body)
        self.assertNotIn(">4기<", body)

    def test_list_labels_match_admin_display(self):
        case_a = _case(
            self.counselor,
            _client_user("목록내담1"),
            "SV-L-1",
            total_sessions=10,
        )
        case_b = _case(
            self.counselor,
            _client_user("목록내담2"),
            "SV-L-2",
            total_sessions=10,
        )
        _intake(case_a)
        for number in range(1, 9):
            _journal(case_a, number, counseling_method=CounselingMethod.IN_PERSON)
        for number in range(1, 11):
            _journal(case_b, number, counseling_method=CounselingMethod.REMOTE)
        completed = _sessions(3, SupervisionSessionStatus.COMPLETED, 7)
        cancelled = _sessions(3, SupervisionSessionStatus.CANCELLED, 1, start=8)
        scheduled = _sessions(3, SupervisionSessionStatus.SCHEDULED, 2, start=9)
        self.assertEqual(len(cancelled) + len(scheduled), 3)
        for session in completed:
            SupervisionAttendance.objects.create(
                session=session,
                counselor=self.counselor.counselor_profile,
                status=SupervisionAttendanceStatus.PRESENT,
            )
        _presentation(self.counselor, "목록보고서", case=case_a, cohort=3)

        admin = _user("sv-list-admin@example.com", "목록관리", UserRole.ADMIN)
        admin_http = HttpClient()
        admin_http.force_login(admin)
        admin_response = admin_http.get(
            reverse("admin_panel:training_progress"), {"cohort": "3"}
        )
        sv_response = self.http.get(
            reverse("supervisor:training_progress"), {"cohort": "3"}
        )
        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(sv_response.status_code, 200)

        admin_row = next(
            row
            for row in admin_response.context["rows"]
            if row["counselor"].pk == self.counselor.pk
        )
        sv_row = next(
            row
            for row in sv_response.context["rows"]
            if row["counselor"].pk == self.counselor.pk
        )
        self.assertEqual(admin_row["display"], sv_row["display"])
        self.assertEqual(admin_row["progress"], sv_row["progress"])
        self.assertContains(sv_response, "총 수련시간")
        self.assertContains(sv_response, admin_row["display"]["intake_label"])
        self.assertContains(sv_response, admin_row["display"]["counseling_label"])
        self.assertContains(sv_response, admin_row["display"]["termination_label"])
        self.assertContains(sv_response, admin_row["display"]["supervision_label"])
        self.assertContains(sv_response, admin_row["display"]["case_report_label"])
        self.assertContains(sv_response, admin_row["display"]["training_time_label"])
        self.assertNotContains(sv_response, "대면 ")
        self.assertNotContains(sv_response, "비대면 ")


class SupervisorTrainingProgressDetailTests(TestCase):
    def setUp(self):
        self.supervisor = _supervisor("sv-detail@example.com", "상세수퍼", [6])
        self.counselor = _counselor("상세대상", 6)
        self.http = HttpClient()
        self.http.force_login(self.supervisor)
        self.url = reverse(
            "supervisor:counselor_training_progress_detail",
            kwargs={"counselor_id": self.counselor.pk},
        )

    def test_detail_matches_admin_and_shows_method_summary(self):
        in_person = _case(
            self.counselor, _client_user("상세대면"), "SV-D-IP", total_sessions=10
        )
        remote = _case(
            self.counselor, _client_user("상세비대면"), "SV-D-RM", total_sessions=10
        )
        Case.objects.filter(pk=remote.pk).update(counseling_method=CounselingMethod.REMOTE)
        remote.refresh_from_db()
        for number in range(1, 9):
            _journal(in_person, number, counseling_method=None)
        for number in range(1, 11):
            _journal(remote, number, counseling_method=None)

        admin = _user("sv-detail-admin@example.com", "상세관리", UserRole.ADMIN)
        admin_http = HttpClient()
        admin_http.force_login(admin)
        admin_detail = admin_http.get(
            reverse(
                "admin_panel:counselor_training_progress_detail",
                kwargs={"counselor_id": self.counselor.pk},
            )
        )
        sv_detail = self.http.get(self.url)
        self.assertEqual(admin_detail.status_code, 200)
        self.assertEqual(sv_detail.status_code, 200)
        self.assertEqual(
            admin_detail.context["training_progress"],
            sv_detail.context["training_progress"],
        )
        progress = get_counselor_training_progress(self.counselor)
        self.assertEqual(sv_detail.context["training_progress"], progress)
        self.assertContains(sv_detail, "대면 8시간 / 비대면 10시간")
        self.assertContains(sv_detail, "총 18시간")
        self.assertNotContains(sv_detail, "접수면접·상담자 경험·수퍼비전 출석 합계")
        self.assertNotContains(sv_detail, "회기·세션에 저장된 방식 기준")
        self.assertContains(sv_detail, reverse("supervisor:counselor_training_certificate", args=[self.counselor.pk]))


class SupervisorTrainingProgressParityTests(TestCase):
    def test_batch_and_individual_match_admin_helpers(self):
        counselor = _counselor("일치상담", 7)
        case = _case(counselor, _client_user("일치내담"), "SV-P-1")
        _intake(case)
        for number in range(1, 6):
            _journal(case, number, counseling_method=CounselingMethod.REMOTE)
        individual = get_counselor_training_progress(counselor)
        batch = get_cohort_counselors_training_progress(7)
        batch_progress = next(
            entry["progress"] for entry in batch if entry["counselor"].pk == counselor.pk
        )
        for key in PROGRESS_COMPARE_KEYS:
            self.assertEqual(individual[key], batch_progress[key], key)

        supervisor = _supervisor("sv-parity@example.com", "일치수퍼", [7])
        http = HttpClient()
        http.force_login(supervisor)
        listing = http.get(reverse("supervisor:training_progress"), {"cohort": "7"})
        detail = http.get(
            reverse(
                "supervisor:counselor_training_progress_detail",
                kwargs={"counselor_id": counselor.pk},
            )
        )
        row = listing.context["rows"][0]
        for key in PROGRESS_COMPARE_KEYS:
            self.assertEqual(row["progress"][key], individual[key], key)
            self.assertEqual(detail.context["training_progress"][key], individual[key], key)


class SupervisorTrainingProgressQueryBudgetTests(TestCase):
    def _seed(self, count, cohort):
        counselors = []
        for index in range(count):
            counselor = _counselor(f"수퍼성능{cohort}-{index:02d}", cohort)
            case = _case(
                counselor,
                _client_user(f"수퍼성능내담{cohort}-{index}"),
                f"SV-Q-{cohort}-{index}",
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

    def test_list_view_query_count_does_not_grow_with_counselor_count(self):
        supervisor = _supervisor("sv-q@example.com", "질의수퍼", [61, 62, 63])
        http = HttpClient()
        http.force_login(supervisor)
        url = reverse("supervisor:training_progress")

        self._seed(1, cohort=61)
        with CaptureQueriesContext(connection) as one:
            response = http.get(url, {"cohort": "61"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["rows"]), 1)
        one_count = len(one.captured_queries)

        self._seed(5, cohort=62)
        with CaptureQueriesContext(connection) as five:
            response = http.get(url, {"cohort": "62"})
        self.assertEqual(len(response.context["rows"]), 5)
        five_count = len(five.captured_queries)

        self._seed(20, cohort=63)
        with CaptureQueriesContext(connection) as twenty:
            response = http.get(url, {"cohort": "63"})
        self.assertEqual(len(response.context["rows"]), 20)
        twenty_count = len(twenty.captured_queries)

        self.assertEqual(one_count, five_count)
        self.assertEqual(five_count, twenty_count)
        self.assertLessEqual(twenty_count, 20)


class SupervisorTrainingCertificateReuseTests(TestCase):
    def test_certificate_uses_shared_pdf_and_matches_admin(self):
        supervisor = _supervisor("sv-cert@example.com", "확인증수퍼", [8])
        counselor = _counselor("확인증대상", 8)
        case = _case(counselor, _client_user("확인증내담"), "SV-C-1")
        for number in range(1, 6):
            _journal(case, number, counseling_method=CounselingMethod.IN_PERSON)

        admin = _user("sv-cert-admin@example.com", "확인증관리", UserRole.ADMIN)
        sv_http = HttpClient()
        admin_http = HttpClient()
        sv_http.force_login(supervisor)
        admin_http.force_login(admin)
        sv_response = sv_http.get(
            reverse(
                "supervisor:counselor_training_certificate",
                kwargs={"counselor_id": counselor.pk},
            )
        )
        admin_response = admin_http.get(
            reverse(
                "admin_panel:counselor_training_certificate",
                kwargs={"counselor_id": counselor.pk},
            )
        )
        self.assertEqual(sv_response.status_code, 200)
        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(sv_response["Content-Type"], "application/pdf")
        self.assertEqual(admin_response["Content-Type"], "application/pdf")
        self.assertTrue(sv_response.content.startswith(b"%PDF"))
        self.assertTrue(admin_response.content.startswith(b"%PDF"))

        from apps.counseling.training_certificate import (
            certificate_date_for_counselor,
            counseling_cases_5plus_count,
            supervisor_label_for_counselor,
        )
        from apps.counseling.training_certificate_pdf import (
            build_training_certificate_context,
        )

        progress = get_counselor_training_progress(counselor)
        context = build_training_certificate_context(
            counselor,
            progress,
            certificate_date_for_counselor(counselor),
            supervisor_label=supervisor_label_for_counselor(counselor),
            counseling_cases_5plus_count=counseling_cases_5plus_count(counselor),
        )
        self.assertEqual(context["columns"], ["수련항목", "수련시간", "진행방식", "비고"])
        self.assertEqual(context["rows"][1][3], "5회기 이상 지속된 사례 총 1건")
        self.assertEqual(context["rows"][1][1], "5시간")
        self.assertEqual(context["rows"][1][2], "대면")
        self.assertEqual(context["rows"][0][0], "접수면접")
        self.assertEqual(context["rows"][2][0], "수퍼비전")
        self.assertEqual(context["rows"][3][0], "사례보고서")
        # 관리자 URL은 여전히 ADMIN 전용 (수퍼바이저는 전용 endpoint만 사용).
        forbidden = HttpClient()
        forbidden.force_login(supervisor)
        admin_url = reverse(
            "admin_panel:counselor_training_certificate",
            kwargs={"counselor_id": counselor.pk},
        )
        self.assertEqual(forbidden.get(admin_url).status_code, 403)
