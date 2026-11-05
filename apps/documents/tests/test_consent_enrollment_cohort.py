import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
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
class ConsentEnrollmentCohortTests(TestCase):
    def setUp(self):
        self.counselor = User.objects.create_user(
            email="consent-multi@example.com",
            password="pass12345",
            name="홍길동",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.update_or_create(
            user=self.counselor,
            defaults={"cohort": 2, "is_approved": True},
        )
        self.legacy = User.objects.create_user(
            email="consent-legacy@example.com",
            password="pass12345",
            name="단일상담",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.update_or_create(
            user=self.legacy,
            defaults={"cohort": 7, "is_approved": True},
        )
        self.other = User.objects.create_user(
            email="consent-other@example.com",
            password="pass12345",
            name="김상담",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        self.admin = User.objects.create_user(
            email="consent-admin@example.com",
            password="pass12345",
            name="관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
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
        self.client_one = self._client("c-one@example.com", "내담1기")
        self.client_two = self._client("c-two@example.com", "내담2기")
        self.client_unknown = self._client("c-unknown@example.com", "내담미정")
        self.client_legacy = self._client("c-legacy@example.com", "내담레거시")
        self.client_open = self._client("c-open@example.com", "내담신청")
        self.case_one = self._case(self.counselor, self.client_one, "CON-1", self.first)
        self.case_two = self._case(self.counselor, self.client_two, "CON-2", self.second)
        self.case_unknown = self._case(self.counselor, self.client_unknown, "CON-U", None)
        self.case_legacy = self._case(self.legacy, self.client_legacy, "CON-L", None)
        self.consent_one = self._consent(self.case_one)
        self.consent_two = self._consent(self.case_two)
        self.consent_unknown = self._consent(self.case_unknown)
        self.consent_legacy = self._consent(self.case_legacy)
        self.http = Client()

    def _client(self, email, name):
        return User.objects.create_user(
            email=email,
            password="pass12345",
            name=name,
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )

    def _case(self, counselor, client, case_number, enrollment):
        application = CounselingApplication.objects.create(
            client=client,
            counseling_types=["개인상담"],
            reason="동의서 기수",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.IN_PROGRESS,
        )
        return Case.objects.create(
            application=application,
            client=client,
            counselor=counselor,
            training_enrollment=enrollment,
            case_number=case_number,
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.IN_PERSON,
        )

    def _pdf(self):
        return SimpleUploadedFile("scan.pdf", b"%PDF-1.4 test", content_type="application/pdf")

    def _consent(self, case):
        return upsert_counselor_consent(
            case=case,
            doc_type=ConsentDocType.PRIVACY,
            file_obj=self._pdf(),
            uploaded_by=case.counselor,
        )

    def test_admin_lists_consents_by_case_enrollment(self):
        self.http.login(email="consent-admin@example.com", password="pass12345")
        url = reverse("admin_panel:consent_submissions")

        cohort_one = self.http.get(url, {"cohort": "1"})
        self.assertContains(cohort_one, "내담1기")
        self.assertNotContains(cohort_one, "내담2기")
        self.assertNotContains(cohort_one, "내담미정")
        self.assertContains(cohort_one, 'value="1"')

        cohort_two = self.http.get(url, {"cohort": "2"})
        self.assertContains(cohort_two, "내담2기")
        self.assertNotContains(cohort_two, "내담1기")
        self.assertNotContains(cohort_two, "내담미정")

        legacy = self.http.get(url, {"cohort": "7"})
        self.assertContains(legacy, "내담레거시")
        self.assertNotContains(legacy, "내담1기")

        everyone = self.http.get(url, {"cohort": "all"})
        self.assertContains(everyone, "내담미정")
        self.assertContains(everyone, "내담1기")
        self.assertContains(everyone, "내담2기")

    def test_download_filename_uses_case_cohort_without_rewriting_storage(self):
        stored = self.consent_one.file.name
        self.assertIn("[2기]", stored)
        filename = self.consent_one.get_download_filename()
        self.assertIn("[1기]홍길동_내담1기_개인정보동의서", filename)
        self.assertTrue(filename.endswith(".pdf"))
        self.assertIn("[2기]홍길동_내담2기_개인정보동의서", self.consent_two.get_download_filename())
        self.assertIn("[기수미정]", self.consent_unknown.get_download_filename())
        self.assertNotIn("[2기]", self.consent_unknown.get_download_filename())
        self.assertIn("[7기]단일상담_내담레거시_개인정보동의서", self.consent_legacy.get_download_filename())

        self.http.login(email="consent-admin@example.com", password="pass12345")
        url = reverse("documents:consent_file", kwargs={"pk": self.consent_one.pk})
        response = self.http.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 test")
        self.consent_one.refresh_from_db()
        self.assertEqual(self.consent_one.file.name, stored)

    def test_consent_without_case_is_not_assigned_to_profile_cohort(self):
        application = CounselingApplication.objects.create(
            client=self.client_open,
            counseling_types=["개인상담"],
            reason="사례 없음",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.WAITING_MATCH,
        )
        consent = ConsentDocument(
            client=self.client_open,
            application=application,
            doc_type=ConsentDocType.PRIVACY,
            uploaded_by=self.counselor,
        )
        consent.file.save("scan.pdf", self._pdf(), save=True)
        self.assertIsNone(consent.record_cohort_for_download())
        self.assertIn("[기수미정]", consent.get_download_filename())
        self.assertNotIn("[2기]", consent.get_download_filename())

        self.http.login(email="consent-admin@example.com", password="pass12345")
        response = self.http.get(reverse("admin_panel:consent_submissions"), {"cohort": "2"})
        self.assertNotContains(response, "내담신청")

    def test_download_permission_stays_with_admin_and_assigned_counselor(self):
        url = reverse("documents:consent_file", kwargs={"pk": self.consent_one.pk})
        self.http.login(email="c-one@example.com", password="pass12345")
        self.assertEqual(self.http.get(url).status_code, 403)

        self.http.login(email="consent-other@example.com", password="pass12345")
        self.assertEqual(self.http.get(url).status_code, 403)

        self.http.login(email="consent-multi@example.com", password="pass12345")
        self.assertEqual(self.http.get(url).status_code, 200)

        self.http.login(email="consent-admin@example.com", password="pass12345")
        submissions = self.http.get(reverse("admin_panel:consent_submissions"))
        self.assertEqual(submissions.status_code, 200)

        self.http.login(email="consent-multi@example.com", password="pass12345")
        denied = self.http.get(reverse("admin_panel:consent_submissions"))
        self.assertEqual(denied.status_code, 403)
