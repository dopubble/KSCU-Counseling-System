import io

import pyzipper
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
    PEER_ZIP_PASSWORD = "peer1234"

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
            b"hwp-content-bytes",
            content_type="application/octet-stream",
        )

    def _create_post(self, **kwargs):
        kwargs.setdefault("cohort", 1)
        kwargs.setdefault("author", self.counselor_a)
        kwargs.setdefault("title", "[사례발표] 발표자")
        kwargs.setdefault("file", self.sample_file)
        return CasePresentationPost.objects.create(**kwargs)

    def _extract_zip_with_password(self, zip_bytes: bytes, password: str) -> dict[str, bytes]:
        with pyzipper.AESZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.pwd = password.encode("utf-8")
            return {name: zf.read(name) for name in zf.namelist()}

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

    def test_peer_cannot_download_post_file_without_password(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.counselor_b)
        response = client.get(
            reverse("counselor:presentation_board_post_file", args=[post.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_peer_download_returns_password_protected_zip(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.counselor_b)
        response = client.post(
            reverse("counselor:presentation_board_post_file", args=[post.pk]),
            {"file_password": self.PEER_ZIP_PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn(".zip", response.get("Content-Disposition", ""))

        extracted = self._extract_zip_with_password(response.content, self.PEER_ZIP_PASSWORD)
        self.assertEqual(list(extracted.values())[0], b"hwp-content-bytes")
        post.refresh_from_db()
        self.assertEqual(post.file_password_hash, "")

    def test_peer_short_password_redirects_with_error(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.counselor_b)
        response = client.post(
            reverse("counselor:presentation_board_post_file", args=[post.pk]),
            {"file_password": "abc"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("counselor:presentation_board_detail", args=[post.pk]),
        )

    def test_author_can_download_own_post_without_password(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.counselor_a)
        response = client.get(
            reverse("counselor:presentation_board_post_file", args=[post.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.get("Content-Disposition", ""))
        self.assertIn(".hwp", response.get("Content-Disposition", ""))
        self.assertNotEqual(response["Content-Type"], "application/zip")

    def test_new_post_has_no_stored_password_on_create(self):
        client = Client()
        client.force_login(self.counselor_a)
        response = client.post(
            reverse("counselor:presentation_board_post_create"),
            {
                "cohort": "1",
                "title": "[사례발표] 발표자",
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
        self.assertEqual(post.file_password_hash, "")

        client.force_login(self.counselor_b)
        comment_file = SimpleUploadedFile(
            "concept.hwpx",
            b"hwpx-content",
            content_type="application/octet-stream",
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
        self.assertContains(response, "이 파일에 설정할 암호")
        self.assertContains(response, "presentationBoardFileDownloadModal")

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

    def test_build_password_protected_zip_unit(self):
        from apps.counseling.presentation_file_download import build_password_protected_zip

        zip_bytes = build_password_protected_zip(
            b"hello",
            inner_filename="sample.hwp",
            password="zip1234",
        )
        with pyzipper.AESZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.pwd = b"zip1234"
            self.assertEqual(zf.read("sample.hwp"), b"hello")

    def test_build_password_protected_zip_korean_filename(self):
        from apps.counseling.presentation_file_download import (
            build_password_protected_zip,
            safe_inner_archive_name,
        )

        inner = safe_inner_archive_name("08. (한기상)보고서.hwp")
        zip_bytes = build_password_protected_zip(
            b"content",
            inner_filename="08. (한기상)보고서.hwp",
            password="1234",
        )
        with pyzipper.AESZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.pwd = b"1234"
            self.assertEqual(zf.read(inner), b"content")
