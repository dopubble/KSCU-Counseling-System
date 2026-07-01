from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import CasePresentationComment, CasePresentationPost
from apps.counseling.presentation_board import (
    PRESENTATION_BOARD_COMMENT_CONTENT_TEMPLATE,
    format_presentation_comment_content,
)


class PresentationBoardTests(TestCase):
    def setUp(self):
        self.counselor_a = User.objects.create_user(
            email="presenter@example.com",
            password="pass",
            name="발표자",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        self.counselor_b = User.objects.create_user(
            email="peer@example.com",
            password="pass",
            name="동기상담사",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        self.other_cohort = User.objects.create_user(
            email="other@example.com",
            password="pass",
            name="타기수",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        for user, cohort in (
            (self.counselor_a, 1),
            (self.counselor_b, 1),
            (self.other_cohort, 2),
        ):
            profile = user.counselor_profile
            profile.cohort = cohort
            profile.is_approved = True
            profile.save(update_fields=["cohort", "is_approved", "updated_at"])

        self.sample_file = SimpleUploadedFile(
            "report.hwp",
            b"hwp-content",
            content_type="application/octet-stream",
        )

    def test_cohort_peer_can_view_board(self):
        client = Client()
        client.force_login(self.counselor_b)
        response = client.get(reverse("counselor:presentation_board"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "사례발표 게시판")

    def test_other_cohort_cannot_access_foreign_post_file(self):
        post = CasePresentationPost.objects.create(
            cohort=1,
            author=self.counselor_a,
            title="[사례발표] 발표자",
            file=self.sample_file,
        )
        client = Client()
        client.force_login(self.other_cohort)
        response = client.get(
            reverse("counselor:presentation_board_post_file", args=[post.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_presenter_post_and_peer_comment(self):
        client = Client()
        client.force_login(self.counselor_a)
        response = client.post(
            reverse("counselor:presentation_board_post_create"),
            {
                "cohort": "1",
                "title": "[사례발표] 발표자 — 수퍼비전보고서",
                "content": "",
                "file": self.sample_file,
            },
        )
        self.assertEqual(response.status_code, 302)
        post = CasePresentationPost.objects.get()
        self.assertRedirects(
            response,
            reverse("counselor:presentation_board_detail", args=[post.pk]),
        )
        self.assertEqual(post.cohort, 1)
        self.assertEqual(post.author_id, self.counselor_a.pk)

        client.force_login(self.counselor_b)
        comment_file = SimpleUploadedFile(
            "concept.hwpx",
            b"hwpx-content",
            content_type="application/octet-stream",
        )
        response = client.post(
            reverse("counselor:presentation_board_comment_create", args=[post.pk]),
            {"cohort": "1", "content": "개념화 제출", "file": comment_file},
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response,
            reverse("counselor:presentation_board_detail", args=[post.pk]),
        )
        self.assertEqual(CasePresentationComment.objects.filter(post=post).count(), 1)

    def test_detail_page_peer_sees_comment_form(self):
        post = CasePresentationPost.objects.create(
            cohort=1,
            author=self.counselor_a,
            title="[사례발표] 발표자",
            file=self.sample_file,
        )
        client = Client()
        client.force_login(self.counselor_b)
        response = client.get(
            reverse("counselor:presentation_board_detail", args=[post.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, post.title)
        self.assertContains(response, "사례개념화 연습 댓글달기")
        self.assertContains(response, "사례개념화 연습")
        self.assertContains(response, "10. 예후 및 장애물")
        self.assertNotContains(response, "메모")

    def test_peer_can_comment_without_file(self):
        post = CasePresentationPost.objects.create(
            cohort=1,
            author=self.counselor_a,
            title="[사례발표] 발표자",
            file=self.sample_file,
        )
        client = Client()
        client.force_login(self.counselor_b)
        response = client.post(
            reverse("counselor:presentation_board_comment_create", args=[post.pk]),
            {
                "cohort": "1",
                "content": PRESENTATION_BOARD_COMMENT_CONTENT_TEMPLATE,
            },
        )
        self.assertEqual(response.status_code, 302)
        comment = CasePresentationComment.objects.get(post=post)
        self.assertFalse(comment.file)
        self.assertIn("사례개념화 연습", comment.content)

    def test_detail_shows_full_comment_content(self):
        post = CasePresentationPost.objects.create(
            cohort=1,
            author=self.counselor_a,
            title="[사례발표] 발표자",
            file=self.sample_file,
        )
        long_tail = "10. 예후 및 장애물 — 전체 내용이 보여야 합니다."
        CasePresentationComment.objects.create(
            post=post,
            author=self.counselor_b,
            content=PRESENTATION_BOARD_COMMENT_CONTENT_TEMPLATE + "\n" + long_tail,
        )
        client = Client()
        client.force_login(self.counselor_a)
        response = client.get(
            reverse("counselor:presentation_board_detail", args=[post.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "10. 예후 및 장애물")
        self.assertContains(response, long_tail)
        self.assertNotContains(response, "…")

    def test_detail_comment_accordion_and_participation(self):
        post = CasePresentationPost.objects.create(
            cohort=1,
            author=self.counselor_a,
            title="[사례발표] 발표자",
            file=self.sample_file,
        )
        CasePresentationComment.objects.create(
            post=post,
            author=self.counselor_b,
            content="호소문제\n내용",
        )
        client = Client()
        client.force_login(self.counselor_a)
        response = client.get(
            reverse("counselor:presentation_board_detail", args=[post.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "내용 보기")
        self.assertContains(response, "presentation-comment-collapse")
        self.assertContains(response, "presentation-comment-branch-icon")
        self.assertContains(response, "동기 제출 현황")
        self.assertContains(response, "/ 1명")
        self.assertContains(response, "presentation-comment-section-label")

    def test_format_presentation_comment_highlights_sections(self):
        rendered = str(
            format_presentation_comment_content("호소문제\n\n2. 촉발요인\n일반 내용")
        )
        self.assertIn("presentation-comment-section-label", rendered)
        self.assertIn("호소문제", rendered)
        self.assertIn("2. 촉발요인", rendered)
        self.assertIn("일반 내용", rendered)

    def test_detail_page_author_cannot_comment(self):
        post = CasePresentationPost.objects.create(
            cohort=1,
            author=self.counselor_a,
            title="[사례발표] 발표자",
            file=self.sample_file,
        )
        client = Client()
        client.force_login(self.counselor_a)
        response = client.get(
            reverse("counselor:presentation_board_detail", args=[post.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "사례개념화 연습 댓글달기")

    def test_list_shows_table_not_accordion(self):
        CasePresentationPost.objects.create(
            cohort=1,
            author=self.counselor_a,
            title="[사례발표] 발표자",
            file=self.sample_file,
        )
        client = Client()
        client.force_login(self.counselor_b)
        response = client.get(reverse("counselor:presentation_board"))
        self.assertContains(response, "presentation-board-table")
        self.assertContains(response, "보기")
        self.assertNotContains(response, "accordion")

    def test_presenter_cannot_comment_on_own_post(self):
        post = CasePresentationPost.objects.create(
            cohort=1,
            author=self.counselor_a,
            title="[사례발표] 발표자",
            file=self.sample_file,
        )
        client = Client()
        client.force_login(self.counselor_a)
        response = client.post(
            reverse("counselor:presentation_board_comment_create", args=[post.pk]),
            {
                "cohort": "1",
                "content": "",
                "file": SimpleUploadedFile("x.hwp", b"x", content_type="application/octet-stream"),
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_form_template_download(self):
        client = Client()
        client.force_login(self.counselor_a)
        response = client.get(
            reverse(
                "counselor:presentation_board_form_download",
                args=["supervision_report"],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.get("Content-Disposition", ""))
