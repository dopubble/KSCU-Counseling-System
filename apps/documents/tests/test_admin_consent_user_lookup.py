import tempfile

from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.admin import CounselingApplicationAdmin
from apps.counseling.models import (
    ApplicationStatus,
    CounselingApplication,
    CounselingMethod,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.counseling.services import assign_counselor
from apps.documents.admin import ConsentDocumentAdmin
from apps.documents.models import ConsentDocType, ConsentDocument
from apps.scheduling.admin import AppointmentAdmin
from apps.scheduling.models import Appointment


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
class AdminConsentUserLookupTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="lookup-admin@example.com",
            password="pass12345",
            name="관리자",
        )
        self.counselor = User.objects.create_user(
            email="lookup-counselor@example.com",
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
        self.client_a = User.objects.create_user(
            email="same-a@example.com",
            password="pass12345",
            name="김동일",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.client_b = User.objects.create_user(
            email="same-b@example.com",
            password="pass12345",
            name="김동일",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        app_a = CounselingApplication.objects.create(
            client=self.client_a,
            counseling_types=["개인상담"],
            reason="1기사례",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.WAITING_MATCH,
        )
        app_b = CounselingApplication.objects.create(
            client=self.client_b,
            counseling_types=["개인상담"],
            reason="2기사례",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.WAITING_MATCH,
        )
        self.case_one = assign_counselor(app_a, self.counselor, total_sessions=10)
        self.case_one.training_enrollment = self.enrollment_one
        self.case_one.save(update_fields=["training_enrollment"])
        self.case_two = assign_counselor(app_b, self.counselor, total_sessions=10)
        self.case_two.training_enrollment = self.enrollment_two
        self.case_two.save(update_fields=["training_enrollment"])
        self.http = Client()
        self.http.force_login(self.admin)

    def test_consent_add_uses_the_same_raw_id_search_as_appointments(self):
        self.assertEqual(
            AppointmentAdmin(Appointment, AdminSite()).raw_id_fields,
            ("case", "client", "counselor"),
        )
        self.assertEqual(
            ConsentDocumentAdmin(ConsentDocument, AdminSite()).raw_id_fields,
            ("client", "application", "uploaded_by"),
        )
        add_page = self.http.get(reverse("admin:documents_consentdocument_add"))
        self.assertEqual(add_page.status_code, 200)
        self.assertContains(add_page, 'id="lookup_id_client"')
        self.assertContains(add_page, 'id="lookup_id_application"')
        self.assertContains(add_page, 'id="lookup_id_uploaded_by"')
        self.assertContains(add_page, "파일 교체", count=0)

    def test_user_lookup_finds_name_and_separates_homonyms_by_email(self):
        url = reverse("admin:accounts_user_changelist")
        response = self.http.get(url, {"q": "김동일", "_popup": "1", "_to_field": "id"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "same-a@example.com")
        self.assertContains(response, "same-b@example.com")
        self.assertContains(response, "김동일")

    def test_application_lookup_keeps_cohort_cases_apart(self):
        url = reverse("admin:counseling_counselingapplication_changelist")
        by_name = self.http.get(url, {"q": "김동일", "_popup": "1", "_to_field": "id"})
        self.assertEqual(by_name.status_code, 200)
        self.assertContains(by_name, self.case_one.case_number)
        self.assertContains(by_name, self.case_two.case_number)
        self.assertContains(by_name, "1기")
        self.assertContains(by_name, "2기")
        only_one = self.http.get(
            url, {"q": self.case_one.case_number, "_popup": "1", "_to_field": "id"}
        )
        self.assertContains(only_one, self.case_one.case_number)
        self.assertNotContains(only_one, self.case_two.case_number)
        app_admin = CounselingApplicationAdmin(CounselingApplication, AdminSite())
        request = RequestFactory().get("/")
        request.user = self.admin
        self.assertEqual(app_admin.cohort_display(self.case_one.application), "1기")
        self.assertEqual(app_admin.cohort_display(self.case_two.application), "2기")

    def test_saving_consent_keeps_client_and_application_together(self):
        add_url = reverse("admin:documents_consentdocument_add")
        pdf = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 lookup", content_type="application/pdf")
        mismatched = self.http.post(
            add_url,
            {
                "client": str(self.client_a.pk),
                "application": str(self.case_two.application_id),
                "doc_type": ConsentDocType.COUNSELING,
                "uploaded_by": str(self.counselor.pk),
                "file": pdf,
            },
        )
        self.assertEqual(mismatched.status_code, 200)
        self.assertContains(mismatched, "내담자와 상담 신청의 내담자가 일치하지 않습니다.")
        self.assertFalse(ConsentDocument.objects.exists())
        pdf = SimpleUploadedFile("ok.pdf", b"%PDF-1.4 ok", content_type="application/pdf")
        saved = self.http.post(
            add_url,
            {
                "client": str(self.client_a.pk),
                "application": str(self.case_one.application_id),
                "doc_type": ConsentDocType.COUNSELING,
                "uploaded_by": str(self.counselor.pk),
                "file": pdf,
            },
            follow=True,
        )
        self.assertEqual(saved.status_code, 200)
        consent = ConsentDocument.objects.get()
        self.assertEqual(consent.client_id, self.client_a.pk)
        self.assertEqual(consent.application_id, self.case_one.application_id)
        self.assertEqual(
            consent.application.case.training_enrollment_id, self.enrollment_one.pk
        )
        change = self.http.get(
            reverse("admin:documents_consentdocument_change", args=[consent.pk])
        )
        self.assertContains(change, "파일 교체")
        self.assertContains(change, "현재 파일 보기")

    def test_lookup_does_not_grant_search_to_users_without_admin_access(self):
        counselor_client = Client()
        counselor_client.force_login(self.counselor)
        add_url = reverse("admin:documents_consentdocument_add")
        user_lookup = reverse("admin:accounts_user_changelist")
        self.assertIn(counselor_client.get(add_url).status_code, (302, 403))
        users = counselor_client.get(user_lookup, {"q": "김동일"})
        self.assertIn(users.status_code, (302, 403))
        self.assertNotContains(users, "same-a@example.com", status_code=users.status_code)
