import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    CounselingApplication,
    CounselingMethod,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.counseling.services import assign_counselor
from apps.documents.models import ConsentDocType, ConsentDocument, ConsentDocumentReplacement
from apps.documents.services.consent_service import upsert_counselor_consent
from apps.sessions_app.models import CounselingJournal


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
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
class AdminConsentReplaceTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="replace-client@example.com",
            password="pass12345",
            name="김내담",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.other_client = User.objects.create_user(
            email="replace-other-client@example.com",
            password="pass12345",
            name="이내담",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.counselor = User.objects.create_user(
            email="replace-counselor@example.com",
            password="pass12345",
            name="홍상담",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.update_or_create(
            user=self.counselor,
            defaults={"cohort": 1, "is_approved": True},
        )
        self.enrollment_one = TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
            active_slot=1,
        )
        self.enrollment_two = TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=2,
            status=TrainingEnrollmentStatus.COMPLETED,
            active_slot=None,
        )
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["개인상담"],
            reason="교체검증",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.WAITING_MATCH,
        )
        self.case = assign_counselor(application, self.counselor, total_sessions=10)
        self.case.training_enrollment = self.enrollment_one
        self.case.save(update_fields=["training_enrollment"])
        other_application = CounselingApplication.objects.create(
            client=self.other_client,
            counseling_types=["개인상담"],
            reason="다른사례",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.WAITING_MATCH,
        )
        self.other_case = assign_counselor(other_application, self.counselor, total_sessions=10)
        self.other_case.training_enrollment = self.enrollment_two
        self.other_case.save(update_fields=["training_enrollment"])
        self.admin = User.objects.create_superuser(
            email="replace-admin@example.com",
            password="pass12345",
            name="관리자",
        )
        self.staff_admin = User.objects.create_user(
            email="replace-staff-admin@example.com",
            password="pass12345",
            name="운영관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
            is_staff=True,
        )
        self.supervisor = User.objects.create_user(
            email="replace-sv@example.com",
            password="pass12345",
            name="수퍼바이저",
            role=UserRole.SUPERVISOR,
            status=UserStatus.ACTIVE,
            is_staff=True,
        )
        self.http = Client()
        self.journal = CounselingJournal.objects.create(
            case=self.case,
            counselor=self.counselor,
            session_number=1,
            is_draft=False,
            subjective="기존상담기록유지",
        )

    def _pdf(self, marker, name="scan.pdf"):
        return SimpleUploadedFile(
            name,
            f"%PDF-1.4 {marker}".encode(),
            content_type="application/pdf",
        )

    def _replace_url(self, case, doc_type):
        return reverse(
            "admin:documents_consentdocument_replace",
            args=[case.pk, doc_type],
        )

    def _login_admin(self):
        self.http.force_login(self.admin)

    def _upload(self, case, doc_type, marker, name="old.pdf"):
        return upsert_counselor_consent(
            case=case,
            doc_type=doc_type,
            file_obj=self._pdf(marker, name),
            uploaded_by=self.counselor,
        )

    def _replace(self, case, doc_type, marker, reason="오업로드 교체"):
        return self.http.post(
            self._replace_url(case, doc_type),
            {"file": self._pdf(marker, "new.pdf"), "reason": reason},
        )

    def _storage_has(self, name):
        return storages["consent"].exists(name)

    def _download_body(self, consent):
        url = reverse("documents:consent_file", kwargs={"pk": consent.pk})
        counselor_client = Client()
        counselor_client.force_login(self.counselor)
        response = counselor_client.get(url)
        self.assertEqual(response.status_code, 200)
        return b"".join(response.streaming_content)

    def test_replace_form_shows_case_fields_and_buttons(self):
        consent = self._upload(self.case, ConsentDocType.COUNSELING, "old-counseling")
        self._login_admin()
        change = self.http.get(
            reverse("admin:documents_consentdocument_change", args=[consent.pk])
        )
        self.assertEqual(change.status_code, 200)
        self.assertContains(change, "현재 파일 보기")
        self.assertContains(change, "파일 교체")
        case_change = self.http.get(reverse("admin:counseling_case_change", args=[self.case.pk]))
        self.assertEqual(case_change.status_code, 200)
        self.assertContains(case_change, "상담동의서")
        self.assertContains(case_change, "개인정보동의서")
        self.assertContains(case_change, "접수면접지")
        form = self.http.get(self._replace_url(self.case, ConsentDocType.COUNSELING))
        self.assertEqual(form.status_code, 200)
        self.assertContains(form, "1기")
        self.assertContains(form, "홍상담")
        self.assertContains(form, "김내담")
        self.assertContains(form, self.case.case_number)
        self.assertContains(form, "상담동의서")
        self.assertContains(form, "교체하기")

    def test_replace_counseling_privacy_and_intake(self):
        self._login_admin()
        for doc_type, marker in (
            (ConsentDocType.COUNSELING, "counseling-new"),
            (ConsentDocType.PRIVACY, "privacy-new"),
            (ConsentDocType.INTAKE, "intake-new"),
        ):
            old = self._upload(self.case, doc_type, f"old-{doc_type}")
            old_name = old.file.name
            response = self._replace(self.case, doc_type, marker)
            self.assertEqual(response.status_code, 302, doc_type)
            old.refresh_from_db()
            self.assertTrue(self._storage_has(old_name), doc_type)
            self.assertNotEqual(old.file.name, old_name, doc_type)
            self.assertIn(marker.encode(), self._download_body(old))
            history = ConsentDocumentReplacement.objects.get(consent=old)
            self.assertEqual(history.previous_file_name, old_name)
            self.assertEqual(history.new_file_name, old.file.name)
            self.assertEqual(history.case_id, self.case.pk)
            self.assertEqual(history.doc_type, doc_type)
            self.assertEqual(history.reason, "오업로드 교체")
            self.assertEqual(history.replaced_by_id, self.admin.pk)

    def test_replace_when_file_is_missing_creates_document(self):
        self._login_admin()
        self.assertFalse(
            ConsentDocument.objects.filter(
                application=self.case.application,
                doc_type=ConsentDocType.PRIVACY,
            ).exists()
        )
        response = self._replace(self.case, ConsentDocType.PRIVACY, "first-privacy")
        self.assertEqual(response.status_code, 302)
        consent = ConsentDocument.objects.get(
            application=self.case.application,
            doc_type=ConsentDocType.PRIVACY,
        )
        self.assertTrue(consent.file.name)
        history = ConsentDocumentReplacement.objects.get(consent=consent)
        self.assertEqual(history.previous_file_name, "")
        self.assertIn(b"first-privacy", self._download_body(consent))

    def test_other_case_and_cohort_are_not_changed(self):
        target = self._upload(self.case, ConsentDocType.INTAKE, "target-old")
        other = self._upload(self.other_case, ConsentDocType.INTAKE, "other-keep")
        other_name = other.file.name
        self.other_case.intake_counseling_method = CounselingMethod.IN_PERSON
        self.other_case.save(update_fields=["intake_counseling_method"])
        self._login_admin()
        response = self._replace(self.case, ConsentDocType.INTAKE, "target-new")
        self.assertEqual(response.status_code, 302)
        other.refresh_from_db()
        self.assertEqual(other.file.name, other_name)
        self.assertIn(b"other-keep", self._download_body(other))
        self.other_case.refresh_from_db()
        self.assertEqual(self.other_case.intake_counseling_method, CounselingMethod.IN_PERSON)
        self.assertEqual(self.other_case.training_enrollment_id, self.enrollment_two.pk)
        target.refresh_from_db()
        self.assertIn("[1기]", target.get_download_filename())
        other.refresh_from_db()
        self.assertIn("[2기]", other.get_download_filename())

    def test_invalid_type_and_missing_reason_are_rejected(self):
        self._upload(self.case, ConsentDocType.COUNSELING, "keep")
        consent = ConsentDocument.objects.get(
            application=self.case.application,
            doc_type=ConsentDocType.COUNSELING,
        )
        old_name = consent.file.name
        self._login_admin()
        bad_type = self.http.post(
            self._replace_url(self.case, ConsentDocType.COUNSELING),
            {
                "file": SimpleUploadedFile("note.txt", b"hello", content_type="text/plain"),
                "reason": "잘못된 형식",
            },
        )
        self.assertEqual(bad_type.status_code, 200)
        consent.refresh_from_db()
        self.assertEqual(consent.file.name, old_name)
        missing_reason = self.http.post(
            self._replace_url(self.case, ConsentDocType.COUNSELING),
            {"file": self._pdf("should-not", "new.pdf"), "reason": ""},
        )
        self.assertEqual(missing_reason.status_code, 200)
        consent.refresh_from_db()
        self.assertEqual(consent.file.name, old_name)
        recording = self.http.get(self._replace_url(self.case, ConsentDocType.RECORDING))
        self.assertEqual(recording.status_code, 404)

    def test_staff_admin_can_replace_without_superuser(self):
        self._upload(self.case, ConsentDocType.COUNSELING, "staff-old")
        self.http.force_login(self.staff_admin)
        response = self._replace(self.case, ConsentDocType.COUNSELING, "staff-new")
        self.assertEqual(response.status_code, 302)
        consent = ConsentDocument.objects.get(
            application=self.case.application,
            doc_type=ConsentDocType.COUNSELING,
        )
        self.assertIn(b"staff-new", self._download_body(consent))

    def test_non_admin_users_cannot_replace(self):
        self._upload(self.case, ConsentDocType.COUNSELING, "locked-old")
        url = self._replace_url(self.case, ConsentDocType.COUNSELING)
        for user in (self.counselor, self.supervisor, self.client_user):
            client = Client()
            client.force_login(user)
            response = client.post(
                url,
                {"file": self._pdf("hijack"), "reason": "권한없음"},
            )
            self.assertIn(response.status_code, (302, 403))
        consent = ConsentDocument.objects.get(
            application=self.case.application,
            doc_type=ConsentDocType.COUNSELING,
        )
        self.assertIn(b"locked-old", self._download_body(consent))
        self.assertFalse(ConsentDocumentReplacement.objects.exists())

    def test_previous_file_is_admin_only(self):
        old = self._upload(self.case, ConsentDocType.PRIVACY, "secret-old")
        old_name = old.file.name
        self._login_admin()
        self._replace(self.case, ConsentDocType.PRIVACY, "secret-new")
        history = ConsentDocumentReplacement.objects.get(consent=old)
        previous_url = reverse(
            "admin:documents_consentdocument_replacement_file",
            args=[history.pk, "previous"],
        )
        new_url = reverse(
            "admin:documents_consentdocument_replacement_file",
            args=[history.pk, "new"],
        )
        history_list = reverse("admin:documents_consentdocumentreplacement_changelist")
        for user in (self.counselor, self.supervisor, self.client_user):
            client = Client()
            client.force_login(user)
            self.assertIn(client.get(previous_url).status_code, (302, 403))
            self.assertIn(client.get(new_url).status_code, (302, 403))
            self.assertIn(client.get(history_list).status_code, (302, 403))
        inline = self.http.get(previous_url + "?disposition=inline")
        self.assertEqual(inline.status_code, 200)
        self.assertEqual(inline["Content-Type"], "application/pdf")
        self.assertIn("inline", inline["Content-Disposition"])
        self.assertIn(b"secret-old", b"".join(inline.streaming_content))
        download = self.http.get(previous_url)
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download["Content-Disposition"])
        self.assertIn(b"secret-old", b"".join(download.streaming_content))
        replaced = self.http.get(new_url)
        self.assertEqual(replaced.status_code, 200)
        self.assertEqual(replaced["Content-Type"], "application/pdf")
        self.assertIn(b"secret-new", b"".join(replaced.streaming_content))
        self.assertEqual(self.http.get(history_list).status_code, 200)
        self.assertTrue(self._storage_has(old_name))
        current_url = reverse("documents:consent_file", kwargs={"pk": old.pk})
        counselor_client = Client()
        counselor_client.force_login(self.counselor)
        current = counselor_client.get(current_url)
        self.assertEqual(current.status_code, 200)
        self.assertIn(b"secret-new", b"".join(current.streaming_content))
        client_blocked = Client()
        client_blocked.force_login(self.client_user)
        self.assertEqual(client_blocked.get(current_url).status_code, 403)

    def test_db_failure_keeps_old_file_and_removes_new_file(self):
        consent = self._upload(self.case, ConsentDocType.COUNSELING, "stable-old")
        old_name = consent.file.name
        self._login_admin()
        with patch(
            "apps.documents.services.consent_service.ConsentDocumentReplacement.objects.create",
            side_effect=RuntimeError("db fail"),
        ):
            with self.assertRaises(RuntimeError):
                self._replace(self.case, ConsentDocType.COUNSELING, "unstable-new")
        consent.refresh_from_db()
        self.assertEqual(consent.file.name, old_name)
        self.assertTrue(self._storage_has(old_name))
        stored = [
            path.read_bytes()
            for path in Path(settings.MEDIA_ROOT).rglob("*")
            if path.is_file()
        ]
        self.assertTrue(any(b"stable-old" in data for data in stored))
        self.assertFalse(any(b"unstable-new" in data for data in stored))
        self.assertFalse(ConsentDocumentReplacement.objects.exists())

    def test_counselor_upload_and_journal_stay_intact(self):
        original = self._upload(self.case, ConsentDocType.COUNSELING, "before-upload")
        original_name = original.file.name
        self._login_admin()
        self._replace(self.case, ConsentDocType.COUNSELING, "admin-replaced")
        history = ConsentDocumentReplacement.objects.get(consent=original)
        history_id = history.pk
        previous_name = history.previous_file_name
        admin_name = history.new_file_name
        self.assertEqual(previous_name, original_name)
        counselor_client = Client()
        counselor_client.force_login(self.counselor)
        upload_url = reverse(
            "counselor:consent_upload",
            kwargs={"case_pk": self.case.pk, "doc_type": ConsentDocType.COUNSELING},
        )
        uploaded = counselor_client.post(
            upload_url,
            {"file": self._pdf("counselor-again", "again.pdf")},
            follow=True,
        )
        self.assertEqual(uploaded.status_code, 200)
        consent = ConsentDocument.objects.get(
            application=self.case.application,
            doc_type=ConsentDocType.COUNSELING,
        )
        self.assertEqual(consent.pk, original.pk)
        self.assertNotEqual(consent.file.name, admin_name)
        self.assertIn(b"counselor-again", self._download_body(consent))
        self.assertNotIn(b"admin-replaced", self._download_body(consent))
        self.assertEqual(ConsentDocumentReplacement.objects.filter(consent=consent).count(), 1)
        history.refresh_from_db()
        self.assertEqual(history.pk, history_id)
        self.assertEqual(history.previous_file_name, previous_name)
        self.assertEqual(history.new_file_name, admin_name)
        self.assertEqual(history.reason, "오업로드 교체")
        self.assertEqual(history.replaced_by_id, self.admin.pk)
        self.assertEqual(history.doc_type, ConsentDocType.COUNSELING)
        self.assertEqual(history.case_id, self.case.pk)
        previous_url = reverse(
            "admin:documents_consentdocument_replacement_file",
            args=[history.pk, "previous"],
        )
        previous = self.http.get(previous_url)
        self.assertEqual(previous.status_code, 200)
        self.assertEqual(previous["Content-Type"], "application/pdf")
        self.assertIn(b"before-upload", b"".join(previous.streaming_content))
        self.assertTrue(self._storage_has(previous_name))
        self.journal.refresh_from_db()
        self.assertEqual(self.journal.subjective, "기존상담기록유지")
        self.assertIsNone(self.case.records_submitted_at)
        self.assertEqual(CounselingJournal.objects.filter(case=self.case).count(), 1)
        self.assertTrue(LogEntry.objects.filter(change_message="첨부파일 교체").exists())

    def test_intake_snapshot_is_not_changed_by_admin_replace(self):
        self.case.intake_counseling_method = CounselingMethod.REMOTE
        self.case.save(update_fields=["intake_counseling_method"])
        self._upload(self.case, ConsentDocType.INTAKE, "intake-old")
        self._login_admin()
        self._replace(self.case, ConsentDocType.INTAKE, "intake-new")
        self.case.refresh_from_db()
        self.assertEqual(self.case.intake_counseling_method, CounselingMethod.REMOTE)
        self.assertEqual(self.case.training_enrollment_id, self.enrollment_one.pk)
