import tempfile

from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    CounselingApplication,
    CounselingMethod,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.counseling.services import assign_counselor
from apps.documents.models import COUNSELOR_REQUIRED_DOC_TYPES, ConsentDocType, ConsentDocument
from apps.documents.services.consent_service import replace_consent_file_for_admin
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
class AdminConsentAddTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="add-client@example.com",
            password="pass12345",
            name="김내담",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.counselor = User.objects.create_user(
            email="add-counselor@example.com",
            password="pass12345",
            name="홍상담",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.update_or_create(
            user=self.counselor,
            defaults={"cohort": 1, "is_approved": True},
        )
        TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
            active_slot=1,
        )
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["개인상담"],
            reason="추가검증",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.WAITING_MATCH,
        )
        self.case = assign_counselor(application, self.counselor, total_sessions=10)
        self.case.records_submitted_at = timezone.now()
        self.case.save(update_fields=["records_submitted_at"])
        self.submitted_at = self.case.records_submitted_at
        self.journal = CounselingJournal.objects.create(
            case=self.case,
            counselor=self.counselor,
            session_number=1,
            is_draft=False,
            subjective="기존상담기록유지",
        )
        self.admin = User.objects.create_superuser(
            email="add-admin@example.com",
            password="pass12345",
            name="관리자",
            role=UserRole.ADMIN,
        )
        self.supervisor = User.objects.create_user(
            email="add-sv@example.com",
            password="pass12345",
            name="수퍼바이저",
            role=UserRole.SUPERVISOR,
            status=UserStatus.ACTIVE,
            is_staff=True,
        )
        self.http = Client()
        self.http.force_login(self.admin)

    def _post_add(self, doc_type, marker, http=None):
        client = http or self.http
        pdf = SimpleUploadedFile(
            f"{doc_type}.pdf",
            b"%PDF-1.4 " + marker.encode(),
            content_type="application/pdf",
        )
        return client.post(
            reverse("admin:documents_consentdocument_add"),
            {
                "client": str(self.client_user.pk),
                "application": str(self.case.application_id),
                "doc_type": doc_type,
                "uploaded_by": str(self.admin.pk),
                "file": pdf,
            },
        )

    def test_admin_can_add_intake_counseling_and_privacy_on_locked_case(self):
        add_page = self.http.get(reverse("admin:documents_consentdocument_add"))
        self.assertEqual(add_page.status_code, 200)
        for doc_type, marker in (
            (ConsentDocType.INTAKE, "intake-bytes"),
            (ConsentDocType.COUNSELING, "counseling-bytes"),
            (ConsentDocType.PRIVACY, "privacy-bytes"),
        ):
            response = self._post_add(doc_type, marker)
            self.assertEqual(response.status_code, 302, doc_type)
            self.assertNotIn("/accounts/", response["Location"])
            consent = ConsentDocument.objects.get(
                application=self.case.application,
                doc_type=doc_type,
            )
            self.assertEqual(consent.client_id, self.client_user.pk)
            self.assertTrue(consent.file.name)
            with consent.file.open("rb") as handle:
                self.assertIn(marker.encode(), handle.read())
            follow = self.http.get(response["Location"])
            self.assertEqual(follow.status_code, 200)
            file_page = self.http.get(
                reverse("admin:documents_consentdocument_file", args=[consent.pk]),
                {"disposition": "inline"},
            )
            self.assertEqual(file_page.status_code, 200)
            self.assertIn(marker.encode(), b"".join(file_page.streaming_content))
            log = LogEntry.objects.get(
                content_type=ContentType.objects.get_for_model(ConsentDocument),
                object_id=str(consent.pk),
                action_flag=ADDITION,
            )
            self.assertEqual(log.user_id, self.admin.pk)
            self.assertIn(consent.get_doc_type_display(), log.object_repr)
        self.case.refresh_from_db()
        self.journal.refresh_from_db()
        self.assertEqual(self.case.records_submitted_at, self.submitted_at)
        self.assertEqual(self.journal.subjective, "기존상담기록유지")
        self.assertEqual(self.case.total_sessions, 10)

    def test_duplicate_application_and_doc_type_is_rejected(self):
        first = self._post_add(ConsentDocType.INTAKE, "first")
        self.assertEqual(first.status_code, 302)
        duplicate = self._post_add(ConsentDocType.INTAKE, "second")
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(
            ConsentDocument.objects.filter(
                application=self.case.application,
                doc_type=ConsentDocType.INTAKE,
            ).count(),
            1,
        )
        consent = ConsentDocument.objects.get(
            application=self.case.application,
            doc_type=ConsentDocType.INTAKE,
        )
        with consent.file.open("rb") as handle:
            self.assertIn(b"first", handle.read())

    def test_locked_case_change_is_still_blocked(self):
        created = self._post_add(ConsentDocType.INTAKE, "locked-change")
        self.assertEqual(created.status_code, 302)
        consent = ConsentDocument.objects.get(doc_type=ConsentDocType.INTAKE)
        change = self.http.post(
            reverse("admin:documents_consentdocument_change", args=[consent.pk]),
            {
                "client": str(self.client_user.pk),
                "application": str(self.case.application_id),
                "doc_type": ConsentDocType.INTAKE,
                "uploaded_by": str(self.admin.pk),
            },
        )
        self.assertEqual(change.status_code, 403)

    def test_replace_still_works_after_admin_add(self):
        created = self._post_add(ConsentDocType.COUNSELING, "before-replace")
        self.assertEqual(created.status_code, 302)
        consent = ConsentDocument.objects.get(doc_type=ConsentDocType.COUNSELING)
        old_name = consent.file.name
        replacement = SimpleUploadedFile(
            "replaced.pdf",
            b"%PDF-1.4 after-replace",
            content_type="application/pdf",
        )
        replace_consent_file_for_admin(
            case=self.case,
            doc_type=ConsentDocType.COUNSELING,
            file_obj=replacement,
            replaced_by=self.admin,
            reason="운영 교체 유지",
        )
        consent.refresh_from_db()
        self.assertNotEqual(consent.file.name, old_name)
        with consent.file.open("rb") as handle:
            self.assertIn(b"after-replace", handle.read())
        self.assertEqual(consent.replacements.count(), 1)

    def test_counselor_client_supervisor_cannot_add_in_admin(self):
        add_url = reverse("admin:documents_consentdocument_add")
        for user in (self.counselor, self.client_user, self.supervisor):
            session = Client()
            session.force_login(user)
            get_status = session.get(add_url).status_code
            self.assertIn(get_status, (302, 403), user.role)
            posted = self._post_add(ConsentDocType.INTAKE, "blocked", http=session)
            self.assertIn(posted.status_code, (302, 403), user.role)
        self.assertFalse(ConsentDocument.objects.exists())

    def test_recording_consent_stays_locked_on_submitted_case(self):
        self.assertNotIn(ConsentDocType.RECORDING, COUNSELOR_REQUIRED_DOC_TYPES)
        response = self._post_add(ConsentDocType.RECORDING, "recording-blocked")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ConsentDocument.objects.exists())
        self.case.refresh_from_db()
        self.assertEqual(self.case.records_submitted_at, self.submitted_at)
