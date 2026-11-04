"""대시보드에서 고른 Enrollment가 사례 목록과 사례발표 게시판으로 이어진다."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CasePresentationPost,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)


def _user(email, name, role):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


def _counselor(name, cohort):
    user = _user(f"{name}@board.example.com", name, UserRole.COUNSELOR)
    profile = user.counselor_profile
    profile.cohort = cohort
    profile.is_approved = True
    profile.save(update_fields=["cohort", "is_approved", "updated_at"])
    return user


def _case(counselor, number, *, enrollment, status=CaseStatus.ACTIVE):
    client = _user(f"{number}@board-client.example.com", number, UserRole.CLIENT)
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
        status=status,
        counseling_method=CounselingMethod.IN_PERSON,
        total_sessions=4,
        remaining_sessions=4,
    )


def _post(author, title, cohort):
    return CasePresentationPost.objects.create(
        cohort=cohort,
        author=author,
        title=title,
        file=SimpleUploadedFile(f"{title}.pdf", b"%PDF-1.4"),
    )


class DashboardEnrollmentCaseAndBoardTests(TestCase):
    def setUp(self):
        self.counselor = _counselor("홍길동", 2)
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
        self.case_one = _case(self.counselor, "BOARD-1", enrollment=self.first)
        self.case_one_closed = _case(
            self.counselor,
            "BOARD-1C",
            enrollment=self.first,
            status=CaseStatus.CLOSED,
        )
        self.case_two = _case(self.counselor, "BOARD-2", enrollment=self.second)
        self.post_one = _post(self.counselor, "1기발표", 1)
        self.post_two = _post(self.counselor, "2기발표", 2)
        self.http = Client()
        self.http.force_login(self.counselor)
        self.dashboard = reverse("counselor:dashboard")
        self.board = reverse("counselor:presentation_board")

    def test_selected_enrollment_lists_only_its_cases(self):
        first = self.http.get(self.dashboard, {"enrollment": str(self.first.pk)})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(
            [case.pk for case in first.context["cases"]],
            [self.case_one.pk],
        )
        self.assertEqual(
            [case.pk for case in first.context["closed_cases"]],
            [self.case_one_closed.pk],
        )
        self.assertContains(first, "BOARD-1")
        self.assertNotContains(first, "BOARD-2")
        self.assertContains(first, f"{self.board}?cohort=1")

        second = self.http.get(self.dashboard, {"enrollment": str(self.second.pk)})
        self.assertEqual(
            [case.pk for case in second.context["cases"]],
            [self.case_two.pk],
        )
        self.assertEqual(list(second.context["closed_cases"]), [])
        self.assertContains(second, "BOARD-2")
        self.assertNotContains(second, "BOARD-1")
        self.assertContains(second, f"{self.board}?cohort=2")

    def test_case_detail_stays_open_for_both_enrollments(self):
        for case in (self.case_one, self.case_two, self.case_one_closed):
            response = self.http.get(reverse("counselor:case_detail", args=[case.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, case.case_number)

    def test_dashboard_board_link_opens_that_cohort(self):
        first = self.http.get(f"{self.board}?cohort=1")
        self.assertEqual(first.status_code, 200)
        self.assertContains(first, "1기발표")
        self.assertNotContains(first, "2기발표")
        self.assertContains(first, "1기 게시글")

        second = self.http.get(f"{self.board}?cohort=2")
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, "2기발표")
        self.assertNotContains(second, "1기발표")
        self.assertContains(second, "2기 게시글")

    def test_completed_cohort_board_is_readable_and_detail_returns_there(self):
        detail = self.http.get(
            reverse("counselor:presentation_board_detail", args=[self.post_one.pk])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "1기발표")
        self.assertContains(detail, f"{self.board}?cohort=1")
        self.assertNotContains(detail, "보고서 등록하기")

    def test_other_enrollment_and_unjoined_cohort_are_blocked(self):
        other = _counselor("다른사람", 4)
        secret = TrainingEnrollment.objects.create(
            counselor=other,
            cohort=4,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        response = self.http.get(self.dashboard, {"enrollment": str(secret.pk)})
        self.assertEqual(response.status_code, 404)

        blocked = self.http.get(f"{self.board}?cohort=3")
        self.assertEqual(blocked.status_code, 403)
        foreign_post = _post(other, "4기발표", 4)
        foreign_detail = self.http.get(
            reverse("counselor:presentation_board_detail", args=[foreign_post.pk])
        )
        self.assertEqual(foreign_detail.status_code, 403)

    def test_single_cohort_counselor_keeps_the_existing_board(self):
        counselor = _counselor("단일", 3)
        enrollment = TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=3,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        case = _case(counselor, "BOARD-ONE", enrollment=enrollment)
        post = _post(counselor, "단일발표", 3)
        http = Client()
        http.force_login(counselor)
        dashboard = http.get(reverse("counselor:dashboard"))
        self.assertContains(dashboard, "BOARD-ONE")
        self.assertEqual(dashboard.context["selected_training_enrollment"], enrollment)
        board = http.get(reverse("counselor:presentation_board"))
        self.assertEqual(board.status_code, 200)
        self.assertContains(board, "단일발표")
        self.assertContains(board, "보고서 등록하기")
        detail = http.get(reverse("counselor:case_detail", args=[case.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(post.cohort, 3)

    def test_multi_cohort_counselor_cannot_attach_case_without_enrollment(self):
        unscoped = _case(self.counselor, "BOARD-NONE", enrollment=None)
        board = self.http.get(f"{self.board}?cohort=2")
        self.assertNotContains(board, "BOARD-NONE")
        self.assertContains(board, "BOARD-2")
        response = self.http.post(
            reverse("counselor:presentation_board_post_create"),
            {
                "cohort": "2",
                "title": "무기수연결",
                "content": "",
                "case": str(unscoped.pk),
                "file": SimpleUploadedFile("unscoped.pdf", b"%PDF-1.4"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CasePresentationPost.objects.filter(case=unscoped).count(), 0)
        link = self.http.post(
            reverse("counselor:presentation_board_post_case", args=[self.post_two.pk]),
            {"case": str(unscoped.pk)},
        )
        self.assertEqual(link.status_code, 302)
        self.post_two.refresh_from_db()
        self.assertIsNone(self.post_two.case_id)

    def test_create_rejects_a_case_from_another_enrollment(self):
        response = self.http.post(
            reverse("counselor:presentation_board_post_create"),
            {
                "cohort": "2",
                "title": "기수불일치",
                "content": "",
                "case": str(self.case_one.pk),
                "file": SimpleUploadedFile("mismatch.pdf", b"%PDF-1.4"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CasePresentationPost.objects.filter(title="기수불일치").count(), 0)
        self.case_one.refresh_from_db()
        self.assertEqual(self.case_one.training_enrollment_id, self.first.pk)

    def test_completed_cohort_does_not_gain_a_new_post(self):
        before = CasePresentationPost.objects.count()
        response = self.http.post(
            reverse("counselor:presentation_board_post_create"),
            {
                "cohort": "1",
                "title": "과거작성",
                "content": "",
                "case": str(self.case_one.pk),
                "file": SimpleUploadedFile("old.pdf", b"%PDF-1.4"),
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(CasePresentationPost.objects.count(), before)

    def test_training_progress_still_follows_the_selected_enrollment(self):
        response = self.http.get(self.dashboard, {"enrollment": str(self.first.pk)})
        self.assertEqual(response.context["selected_training_enrollment"], self.first)
        self.assertEqual(response.context["certificate_approval"]["status"], "unapproved")

    def test_admin_and_supervisor_board_access_stays_open(self):
        admin = _user("admin@board.example.com", "관리자", UserRole.ADMIN)
        supervisor = _user("sup@board.example.com", "수퍼", UserRole.SUPERVISOR)
        for user in (admin, supervisor):
            http = Client()
            http.force_login(user)
            response = http.get(self.board)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "1기발표")
            self.assertContains(response, "2기발표")
            detail = http.get(
                reverse("counselor:presentation_board_detail", args=[self.post_one.pk])
            )
            self.assertEqual(detail.status_code, 200)
