from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import CasePresentationComment, CasePresentationPost
from apps.counseling.presentation_board import (
    PRESENTATION_BOARD_COMMENT_CONTENT_TEMPLATE,
    default_presentation_post_title,
    format_presentation_comment_content,
)

MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF\n"
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
            "report.pdf",
            MINIMAL_PDF,
            content_type="application/pdf",
        )

    def _create_post(self, **kwargs):
        kwargs.setdefault("cohort", 1)
        kwargs.setdefault("author", self.counselor_a)
        kwargs.setdefault("title", default_presentation_post_title("발표자"))
        kwargs.setdefault("file", self.sample_file)
        return CasePresentationPost.objects.create(**kwargs)

    def test_cohort_peer_can_view_board(self):
        client = Client()
        client.force_login(self.counselor_b)
        response = client.get(reverse("counselor:presentation_board"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "사례발표 게시판")

    def test_other_cohort_cannot_access_foreign_post_file(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.other_cohort)
        response = client.get(
            reverse("counselor:presentation_board_post_file", args=[post.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_peer_get_post_file_requires_password(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.counselor_b)
        response = client.get(
            reverse("counselor:presentation_board_post_file", args=[post.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_peer_post_returns_encrypted_pdf(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.counselor_b)
        response = client.post(
            reverse("counselor:presentation_board_post_file", args=[post.pk]),
            {"file_password": "peer1234"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response.get("Content-Disposition", ""))

    def test_author_sees_filename_download_button_on_detail(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.counselor_a)
        response = client.get(
            reverse("counselor:presentation_board_detail", args=[post.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "presentationBoardFileDownloadModal")
        self.assertContains(response, "data-bs-toggle=\"modal\"")
        self.assertContains(response, ".pdf")
        self.assertNotContains(
            response,
            f'href="{reverse("counselor:presentation_board_post_file", args=[post.pk])}"',
        )

    def test_author_get_post_file_requires_password(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.counselor_a)
        response = client.get(
            reverse("counselor:presentation_board_post_file", args=[post.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_sees_filename_download_button_on_detail(self):
        post = self._create_post()
        admin = User.objects.create_user(
            email="admin@example.com",
            password="pass",
            name="관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        client = Client()
        client.force_login(admin)
        response = client.get(
            reverse("counselor:presentation_board_detail", args=[post.pk])
            + f"?cohort={post.cohort}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "presentationBoardFileDownloadModal")
        self.assertContains(response, ".pdf")
        self.assertNotContains(
            response,
            f'href="{reverse("counselor:presentation_board_post_file", args=[post.pk])}"',
        )

    def test_post_create_rejects_non_pdf(self):
        client = Client()
        client.force_login(self.counselor_a)
        hwp_file = SimpleUploadedFile(
            "report.hwp",
            b"hwp-content",
            content_type="application/octet-stream",
        )
        response = client.post(
            reverse("counselor:presentation_board_post_create"),
            {
                "cohort": "1",
                "title": default_presentation_post_title("발표자"),
                "content": "",
                "file": hwp_file,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CasePresentationPost.objects.count(), 0)

    def test_comment_create_rejects_non_pdf_attachment(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.counselor_b)
        hwp_file = SimpleUploadedFile(
            "concept.hwp",
            b"hwp-content",
            content_type="application/octet-stream",
        )
        response = client.post(
            reverse("counselor:presentation_board_comment_create", args=[post.pk]),
            {
                "cohort": "1",
                "content": "개념화 제출",
                "file": hwp_file,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CasePresentationComment.objects.count(), 0)

    def test_new_post_has_no_stored_password_on_create(self):
        client = Client()
        client.force_login(self.counselor_a)
        response = client.post(
            reverse("counselor:presentation_board_post_create"),
            {
                "cohort": "1",
                "title": default_presentation_post_title("발표자"),
                "content": "",
                "file": self.sample_file,
            },
        )
        self.assertEqual(response.status_code, 302)
        post = CasePresentationPost.objects.get()
        self.assertEqual(post.file_password_hash, "")

    def test_presenter_post_and_peer_comment(self):
        client = Client()
        client.force_login(self.counselor_a)
        response = client.post(
            reverse("counselor:presentation_board_post_create"),
            {
                "cohort": "1",
                "title": "임의로 바꾼 제목",
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
        self.assertEqual(post.title, default_presentation_post_title("발표자"))
        self.assertEqual(post.file_password_hash, "")

        client.force_login(self.counselor_b)
        comment_file = SimpleUploadedFile(
            "concept.pdf",
            MINIMAL_PDF,
            content_type="application/pdf",
        )
        response = client.post(
            reverse("counselor:presentation_board_comment_create", args=[post.pk]),
            {
                "cohort": "1",
                "content": "개념화 제출",
                "file": comment_file,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response,
            reverse("counselor:presentation_board_detail", args=[post.pk]),
        )
        self.assertEqual(CasePresentationComment.objects.filter(post=post).count(), 1)

    def test_post_modal_shows_auto_title_readonly(self):
        client = Client()
        client.force_login(self.counselor_a)
        response = client.get(reverse("counselor:presentation_board"))
        self.assertEqual(response.status_code, 200)
        expected_title = default_presentation_post_title("발표자")
        self.assertContains(response, expected_title)
        self.assertContains(response, 'id="presentationPostTitle"')
        self.assertContains(response, "readonly")

    def test_detail_page_peer_sees_comment_form(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.counselor_b)
        response = client.get(
            reverse("counselor:presentation_board_detail", args=[post.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, post.title)
        self.assertContains(response, "사례개념화 연습 댓글달기")
        self.assertContains(response, "이 PDF에 설정할 암호")
        self.assertContains(response, "presentationBoardFileDownloadModal")
        self.assertContains(response, "file_password")

    def test_peer_can_comment_without_file(self):
        post = self._create_post()
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
        post = self._create_post()
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
        post = self._create_post()
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
        self.assertContains(response, "동기 제출 현황")

    def test_format_presentation_comment_highlights_sections(self):
        rendered = str(
            format_presentation_comment_content("호소문제\n\n2. 촉발요인\n일반 내용")
        )
        self.assertIn("presentation-comment-section-label", rendered)
        self.assertIn("호소문제", rendered)

    def test_detail_page_author_cannot_comment(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.counselor_a)
        response = client.get(
            reverse("counselor:presentation_board_detail", args=[post.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "사례개념화 연습 댓글달기")

    def test_list_shows_table_not_accordion(self):
        self._create_post()
        client = Client()
        client.force_login(self.counselor_b)
        response = client.get(reverse("counselor:presentation_board"))
        self.assertContains(response, "presentation-board-table")

    def test_presenter_cannot_comment_on_own_post(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.counselor_a)
        response = client.post(
            reverse("counselor:presentation_board_comment_create", args=[post.pk]),
            {
                "cohort": "1",
                "content": "",
                "file": SimpleUploadedFile(
                    "x.pdf",
                    MINIMAL_PDF,
                    content_type="application/pdf",
                ),
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

    def test_encrypt_pdf_bytes_unit(self):
        import io

        import pikepdf

        from apps.counseling.presentation_pdf_encrypt import encrypt_pdf_bytes

        encrypted = encrypt_pdf_bytes(MINIMAL_PDF, "pdf1234")
        with pikepdf.open(io.BytesIO(encrypted), password="pdf1234") as pdf:
            self.assertGreaterEqual(len(pdf.pages), 1)
