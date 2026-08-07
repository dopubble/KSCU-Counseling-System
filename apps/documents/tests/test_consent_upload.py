import os
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import ApplicationStatus, CounselingApplication, CounselingMethod
from apps.counseling.services import assign_counselor
from apps.documents.models import ConsentDocType, ConsentDocument
from apps.documents.services.consent_service import upsert_counselor_consent


@override_settings(
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
            },
            "consent": {
                "BACKEND": "apps.documents.storage.ConsentMediaStorage",
                "OPTIONS": {},
            },
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
            },
        },
    MEDIA_ROOT=tempfile.mkdtemp(),
)
class ConsentUploadTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="client@example.com",
            password="pass12345",
            name="이내담",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.counselor = User.objects.create_user(
            email="counselor@example.com",
            password="pass12345",
            name="홍길동",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.update_or_create(
            user=self.counselor,
            defaults={"cohort": 2, "is_approved": True},
        )
        self.other_counselor = User.objects.create_user(
            email="other@example.com",
            password="pass12345",
            name="김상담",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        self.admin = User.objects.create_user(
            email="admin@example.com",
            password="pass12345",
            name="관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["개인상담"],
            reason="테스트",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.WAITING_MATCH,
        )
        self.case = assign_counselor(application, self.counselor, total_sessions=10)
        self.http = Client()

    def _pdf_file(self, name="scan.pdf"):
        return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")

    def test_counselor_can_upload_consent_with_standard_filename(self):
        self.http.login(email="counselor@example.com", password="pass12345")
        url = reverse(
            "counselor:consent_upload",
            kwargs={"case_pk": self.case.pk, "doc_type": ConsentDocType.PRIVACY},
        )
        response = self.http.post(url, {"file": self._pdf_file()}, follow=True)
        self.assertEqual(response.status_code, 200)

        consent = ConsentDocument.objects.get(
            application=self.case.application,
            doc_type=ConsentDocType.PRIVACY,
        )
        self.assertEqual(consent.uploaded_by, self.counselor)
        filename = consent.get_download_filename()
        self.assertIn("[2기]홍길동_이내담_개인정보동의서", filename)
        self.assertTrue(filename.endswith(".pdf"))

    def test_other_counselor_cannot_upload(self):
        self.http.login(email="other@example.com", password="pass12345")
        url = reverse(
            "counselor:consent_upload",
            kwargs={"case_pk": self.case.pk, "doc_type": ConsentDocType.PRIVACY},
        )
        response = self.http.post(url, {"file": self._pdf_file()})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            ConsentDocument.objects.filter(
                application=self.case.application,
                doc_type=ConsentDocType.PRIVACY,
            ).exists()
        )

    def test_reupload_replaces_file_and_keeps_signed_at(self):
        first = upsert_counselor_consent(
            case=self.case,
            doc_type=ConsentDocType.PRIVACY,
            file_obj=self._pdf_file("first.pdf"),
            uploaded_by=self.counselor,
        )
        signed_at = first.signed_at
        old_name = first.file.name

        second = upsert_counselor_consent(
            case=self.case,
            doc_type=ConsentDocType.PRIVACY,
            file_obj=self._pdf_file("second.pdf"),
            uploaded_by=self.counselor,
        )
        second.refresh_from_db()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.signed_at, signed_at)
        self.assertNotEqual(second.file.name, old_name)

    def test_counselor_can_preview_own_consent_inline(self):
        consent = upsert_counselor_consent(
            case=self.case,
            doc_type=ConsentDocType.INTAKE,
            file_obj=self._pdf_file(),
            uploaded_by=self.counselor,
        )
        self.http.login(email="counselor@example.com", password="pass12345")
        url = reverse("documents:consent_file", kwargs={"pk": consent.pk})
        response = self.http.get(url, {"disposition": "inline"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertNotIn("attachment", response["Content-Disposition"].lower())

    def test_client_cannot_access_consent_file(self):
        consent = upsert_counselor_consent(
            case=self.case,
            doc_type=ConsentDocType.COUNSELING,
            file_obj=self._pdf_file(),
            uploaded_by=self.counselor,
        )
        self.http.login(email="client@example.com", password="pass12345")
        url = reverse("documents:consent_file", kwargs={"pk": consent.pk})
        response = self.http.get(url)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_consent_file(self):
        consent = upsert_counselor_consent(
            case=self.case,
            doc_type=ConsentDocType.COUNSELING,
            file_obj=self._pdf_file(),
            uploaded_by=self.counselor,
        )
        self.http.login(email="admin@example.com", password="pass12345")
        url = reverse("documents:consent_file", kwargs={"pk": consent.pk})
        response = self.http.get(url)
        self.assertEqual(response.status_code, 200)

    def test_admin_consent_submissions_filters_by_cohort(self):
        upsert_counselor_consent(
            case=self.case,
            doc_type=ConsentDocType.PRIVACY,
            file_obj=self._pdf_file(),
            uploaded_by=self.counselor,
        )
        self.http.login(email="admin@example.com", password="pass12345")
        url = reverse("admin_panel:consent_submissions")
        response = self.http.get(url, {"cohort": "2"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "홍길동")
        self.assertContains(response, "이내담")

        response_other = self.http.get(url, {"cohort": "9"})
        self.assertEqual(response_other.status_code, 200)
        self.assertNotContains(response_other, "홍길동")

    def test_invalid_extension_rejected(self):
        self.http.login(email="counselor@example.com", password="pass12345")
        url = reverse(
            "counselor:consent_upload",
            kwargs={"case_pk": self.case.pk, "doc_type": ConsentDocType.PRIVACY},
        )
        bad = SimpleUploadedFile("bad.exe", b"data", content_type="application/octet-stream")
        response = self.http.post(url, {"file": bad}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            ConsentDocument.objects.filter(
                application=self.case.application,
                doc_type=ConsentDocType.PRIVACY,
            ).exists()
        )

    def test_counselor_can_delete_uploaded_consent(self):
        consent = upsert_counselor_consent(
            case=self.case,
            doc_type=ConsentDocType.PRIVACY,
            file_obj=self._pdf_file(),
            uploaded_by=self.counselor,
        )
        storage_path = consent.file.path
        self.assertTrue(os.path.exists(storage_path))

        self.http.login(email="counselor@example.com", password="pass12345")
        url = reverse(
            "counselor:consent_delete",
            kwargs={"case_pk": self.case.pk, "doc_type": ConsentDocType.PRIVACY},
        )
        response = self.http.post(
            url,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

        consent.refresh_from_db()
        self.assertFalse(consent.file)
        self.assertFalse(os.path.exists(storage_path))

    def test_other_counselor_cannot_delete_consent(self):
        upsert_counselor_consent(
            case=self.case,
            doc_type=ConsentDocType.PRIVACY,
            file_obj=self._pdf_file(),
            uploaded_by=self.counselor,
        )
        self.http.login(email="other@example.com", password="pass12345")
        url = reverse(
            "counselor:consent_delete",
            kwargs={"case_pk": self.case.pk, "doc_type": ConsentDocType.PRIVACY},
        )
        response = self.http.post(url)
        self.assertEqual(response.status_code, 404)
