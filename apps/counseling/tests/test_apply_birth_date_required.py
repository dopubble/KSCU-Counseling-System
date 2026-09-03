"""상담신청서 생년월일 필수 — 신규 신청만 검증, 기존 빈 값은 조회 유지."""

from datetime import date

from django.test import Client as HttpClient, TestCase
from django.urls import reverse

from apps.accounts.models import ClientProfile, User, UserRole, UserStatus
from apps.counseling.forms import CounselingApplyForm
from apps.counseling.models import ApplicationStatus, CounselingApplication, CounselingMethod
from apps.counseling.services import assign_counselor
from apps.accounts.models import CounselorProfile


def _create_client(name: str) -> User:
    return User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.CLIENT,
        status=UserStatus.ACTIVE,
    )


VALID_APPLY = {
    "name": "신청자",
    "residence_region": "서울",
    "clinical_diagnosis": "없음",
    "current_medication": "없음",
    "counseling_types": ["진로상담"],
    "counseling_method": CounselingMethod.IN_PERSON,
    "preferred_date": "2026-07-01",
    "preferred_time": "14:00",
    "reason": "스트레스",
}


class ApplyBirthDateRequiredTests(TestCase):
    def setUp(self):
        self.user = _create_client("생년월일신청자")
        self.http = HttpClient()
        self.http.force_login(self.user)
        self.url = reverse("counseling:apply")

    def test_apply_page_marks_birth_date_required(self):
        response = self.http.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "생년월일")
        self.assertContains(response, "validateCounselingApplyBirthDate")
        self.assertContains(response, "생년월일은 필수 항목입니다.")

    def test_form_rejects_missing_birth_date(self):
        form = CounselingApplyForm(data=VALID_APPLY, user=self.user)

        self.assertFalse(form.is_valid())
        self.assertIn("birth_date", form.errors)
        self.assertIn("생년월일을 입력해주세요.", form.errors["birth_date"])

    def test_form_accepts_entered_birth_date(self):
        data = {**VALID_APPLY, "birth_date": "1990-05-15"}
        form = CounselingApplyForm(data=data, user=self.user)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["birth_date"], date(1990, 5, 15))

    def test_form_uses_profile_birth_date_when_locked(self):
        ClientProfile.objects.create(user=self.user, birth_date=date(1988, 3, 2))
        form = CounselingApplyForm(data=VALID_APPLY, user=self.user)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["birth_date"], date(1988, 3, 2))

    def test_post_without_birth_date_does_not_save(self):
        response = self.http.post(self.url, VALID_APPLY)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "생년월일을 입력해주세요.")
        self.assertEqual(CounselingApplication.objects.count(), 0)

    def test_post_with_birth_date_creates_application(self):
        response = self.http.post(self.url, {**VALID_APPLY, "birth_date": "1991-12-31"})

        self.assertEqual(response.status_code, 302)
        application = CounselingApplication.objects.get(client=self.user)
        self.assertEqual(application.preferred_schedule.get("birth_date"), "1991-12-31")
        self.user.client_profile.refresh_from_db()
        self.assertEqual(self.user.client_profile.birth_date, date(1991, 12, 31))

    def test_existing_application_without_birth_date_remains_readable(self):
        application = CounselingApplication.objects.create(
            client=self.user,
            counseling_types=["개인상담"],
            reason="과거 신청",
            residence_region="서울",
            clinical_diagnosis="없음",
            current_medication="없음",
            preferred_schedule={"preferred_date": "2026-06-01", "preferred_time": "10:00"},
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.WAITING_MATCH,
        )

        detail = self.http.get(
            reverse("client:application_detail", kwargs={"pk": application.pk})
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "과거 신청")
        application.refresh_from_db()
        self.assertNotIn("birth_date", application.preferred_schedule)
        self.assertEqual(CounselingApplication.objects.get(pk=application.pk).reason, "과거 신청")


class ExistingApplicationWithoutBirthDateMatchingTests(TestCase):
    def test_matching_and_lookup_work_without_birth_date(self):
        client = _create_client("과거내담자")
        counselor = User.objects.create_user(
            email="과거상담사@example.com",
            password="pass12345",
            name="과거상담사",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.get_or_create(user=counselor, defaults={"cohort": 1})
        application = CounselingApplication.objects.create(
            client=client,
            counseling_types=["개인상담"],
            reason="생년월일 없는 기존 신청",
            counseling_method=CounselingMethod.REMOTE,
            status=ApplicationStatus.WAITING_MATCH,
            preferred_schedule={},
        )

        case = assign_counselor(application, counselor, total_sessions=10)

        self.assertEqual(case.application_id, application.pk)
        self.assertEqual(case.counseling_method, CounselingMethod.REMOTE)
        self.assertEqual(
            CounselingApplication.objects.get(pk=application.pk).preferred_schedule,
            {},
        )
