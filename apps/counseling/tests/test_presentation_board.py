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
        with pyzipper.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            zf.setpassword(password.encode("utf-8"))
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

    def _minimal_pdf_bytes(self) -> bytes:
        import pikepdf

        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page()
        buf = io.BytesIO()
        pdf.save(buf)
        return buf.getvalue()

    def test_peer_download_pdf_returns_encrypted_pdf(self):
        post = self._create_post()
        pdf_file = SimpleUploadedFile(
            "report.pdf",
            self._minimal_pdf_bytes(),
            content_type="application/pdf",
        )
        post.file = pdf_file
        post.save(update_fields=["file", "updated_at"])

        client = Client()
        client.force_login(self.counselor_b)
        response = client.post(
            reverse("counselor:presentation_board_post_file", args=[post.pk]),
            {"file_password": self.PEER_ZIP_PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(".pdf", response.get("Content-Disposition", ""))
        self.assertEqual(response.get("X-Presentation-Delivery"), "pdf")

    def test_peer_download_hwp_falls_back_to_zip_without_libreoffice(self):
        post = self._create_post()
        client = Client()
        client.force_login(self.counselor_b)
        response = client.post(
            reverse("counselor:presentation_board_post_file", args=[post.pk]),
            {"file_password": self.PEER_ZIP_PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get("X-Presentation-Delivery"), "zip")
        self.assertEqual(response["Content-Type"], "application/zip")
        extracted = self._extract_zip_with_password(response.content, self.PEER_ZIP_PASSWORD)
        self.assertTrue(extracted)

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

    def test_encrypt_pdf_bytes_unit(self):
        from apps.counseling.presentation_file_download import encrypt_pdf_bytes
        import pikepdf

        raw = self._minimal_pdf_bytes()
        encrypted = encrypt_pdf_bytes(raw, "pdf1234")
        with pikepdf.open(io.BytesIO(encrypted), password="pdf1234") as pdf:
            self.assertGreaterEqual(len(pdf.pages), 1)

    def test_build_password_protected_download_falls_back_when_conversion_pdf_invalid(self):
        from apps.counseling import presentation_file_download as download_mod

        original = download_mod.convert_office_bytes_to_pdf
        download_mod.convert_office_bytes_to_pdf = lambda *args, **kwargs: b"not-a-pdf"
        try:
            payload = download_mod.build_password_protected_download(
                b"hwp-content",
                inner_filename="report.hwp",
                password=self.PEER_ZIP_PASSWORD,
            )
        finally:
            download_mod.convert_office_bytes_to_pdf = original
        self.assertEqual(payload.delivery, "zip")
        extracted = self._extract_zip_with_password(payload.data, self.PEER_ZIP_PASSWORD)
        self.assertTrue(extracted)

    def test_storage_diagnostic_logs_on_missing_file(self):
        from apps.counseling.presentation_file_download import (
            _collect_presentation_file_storage_diagnostics,
            read_uploaded_file_bytes,
        )

        post = self._create_post()
        post.file.name = "presentation_board/missing/on/disk/report.hwp"
        report = _collect_presentation_file_storage_diagnostics(post.file)
        self.assertEqual(report["db_file_name"], post.file.name)
        self.assertIn("media_root", report)

        with self.assertRaises(FileNotFoundError):
            read_uploaded_file_bytes(post.file, display_filename="report.hwp")

    def test_attachment_content_disposition_ascii_fallback(self):
        from apps.counseling.presentation_file_download import attachment_content_disposition
        from django.http import HttpResponse

        header = attachment_content_disposition("08. (한기상)보고서.zip")
        self.assertIn('filename="08.', header)
        self.assertIn("filename*=UTF-8", header)
        response = HttpResponse(b"x")
        response["Content-Disposition"] = header
        response.serialize_headers()

    def test_peer_download_korean_filename_returns_zip(self):
        korean_file = SimpleUploadedFile(
            "08. (한기상)전문상담사 사례발표보고서.hwp",
            b"hwp-bytes",
            content_type="application/octet-stream",
        )
        post = self._create_post(file=korean_file)
        client = Client()
        client.force_login(self.counselor_b)
        response = client.post(
            reverse("counselor:presentation_board_post_file", args=[post.pk]),
            {"file_password": self.PEER_ZIP_PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get("X-Presentation-Delivery"), "zip")
        self.assertIn("attachment", response.get("Content-Disposition", ""))

    def test_build_password_protected_zip_unit(self):
        from apps.counseling.presentation_file_download import build_password_protected_zip

        zip_bytes = build_password_protected_zip(
            b"hello",
            inner_filename="sample.hwp",
            password="zip1234",
        )
        with pyzipper.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            zf.setpassword(b"zip1234")
            self.assertEqual(zf.read("sample.hwp"), b"hello")

    def test_build_password_protected_zip_korean_filename(self):
        from apps.counseling.presentation_file_download import (
            build_password_protected_zip,
            safe_download_basename,
        )

        inner = safe_download_basename("08. (한기상)보고서.hwp")
        zip_bytes = build_password_protected_zip(
            b"content",
            inner_filename="08. (한기상)보고서.hwp",
            password="1234",
        )
        with pyzipper.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            zf.setpassword(b"1234")
            self.assertEqual(zf.read(inner), b"content")
