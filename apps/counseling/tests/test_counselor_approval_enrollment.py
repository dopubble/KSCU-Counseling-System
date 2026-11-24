"""관리자 승인과 동시에 최초 수련 등록을 만든다."""

import re

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.counseling.services import assign_counselor


def _admin():
    return User.objects.create_superuser(
        email="approval-admin@example.com",
        password="Adminpass123!",
        name="승인관리자",
    )


def _counselor(email, name, *, cohort=None, approved=False, status=UserStatus.ACTIVE):
    user = User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=UserRole.COUNSELOR,
        status=status,
    )
    profile = user.counselor_profile
    profile.cohort = cohort
    profile.is_approved = approved
    profile.save(update_fields=["cohort", "is_approved", "updated_at"])
    return user


def _client(name):
    return User.objects.create_user(
        email=f"{name}@approval-client.example.com",
        password="pass12345",
        name=name,
        role=UserRole.CLIENT,
        status=UserStatus.ACTIVE,
    )


def _admin_post(client, url):
    response = client.get(url)
    html = response.content.decode()
    csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    post = {"csrfmiddlewaretoken": csrf.group(1) if csrf else ""}
    for match in re.finditer(r"<input[^>]+name=\"([^\"]+)\"[^>]*>", html):
        name = match.group(1)
        tag = match.group(0)
        if name in post:
            continue
        if 'type="hidden"' in tag or 'type="text"' in tag or 'type="number"' in tag:
            value = re.search(r'value="([^"]*)"', tag)
            post[name] = value.group(1) if value else ""
        elif 'type="checkbox"' in tag:
            post[name] = "on" if "checked" in tag else ""
        else:
            value = re.search(r'value="([^"]*)"', tag)
            if value:
                post[name] = value.group(1)
    for match in re.finditer(
        r'<textarea[^>]+name="([^"]+)"[^>]*>(.*?)</textarea>', html, re.S
    ):
        post[match.group(1)] = match.group(2)
    for match in re.finditer(
        r'<select[^>]+name="([^"]+)"[^>]*>(.*?)</select>', html, re.S
    ):
        selected = re.search(r'<option value="([^"]*)" selected', match.group(2))
        post[match.group(1)] = selected.group(1) if selected else ""
    post["_save"] = "저장"
    return post


class CounselorApprovalEnrollmentTests(TestCase):
    def setUp(self):
        self.admin = _admin()
        self.http = Client()
        self.http.force_login(self.admin)

    def _enrollments(self, counselor):
        return list(
            TrainingEnrollment.objects.filter(counselor=counselor).order_by("cohort")
        )

    def test_profile_approval_creates_one_active_enrollment_and_allows_matching(self):
        counselor = _counselor("new-cohort2@example.com", "신규2기", cohort=2)
        url = reverse("admin:accounts_counselorprofile_change", args=[counselor.counselor_profile.pk])
        post = _admin_post(self.http, url)
        post["cohort"] = "2"
        post["is_approved"] = "on"
        response = self.http.post(url, post, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "신규2기 상담사의 2기 수련 등록을 만들었습니다.")
        counselor.counselor_profile.refresh_from_db()
        self.assertTrue(counselor.counselor_profile.is_approved)
        self.assertEqual(counselor.counselor_profile.cohort, 2)
        enrollment = TrainingEnrollment.objects.get(counselor=counselor)
        self.assertEqual(enrollment.cohort, 2)
        self.assertEqual(enrollment.status, TrainingEnrollmentStatus.ACTIVE)

        application = CounselingApplication.objects.create(
            client=_client("매칭내담"),
            counseling_types=["개인상담"],
            reason="배정",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.WAITING_MATCH,
        )
        case = assign_counselor(application, counselor, total_sessions=8)
        self.assertEqual(case.training_enrollment_id, enrollment.pk)
        self.assertEqual(case.training_enrollment.cohort, 2)

    def test_approval_without_cohort_is_rejected(self):
        counselor = _counselor("no-cohort@example.com", "기수없음")
        url = reverse("admin:accounts_counselorprofile_change", args=[counselor.counselor_profile.pk])
        post = _admin_post(self.http, url)
        post["cohort"] = ""
        post["is_approved"] = "on"
        response = self.http.post(url, post)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "상담사 승인 시 기수를 입력해야 합니다.")
        counselor.counselor_profile.refresh_from_db()
        self.assertFalse(counselor.counselor_profile.is_approved)
        self.assertFalse(TrainingEnrollment.objects.filter(counselor=counselor).exists())

    def test_direct_profile_save_does_not_create_an_enrollment(self):
        counselor = _counselor("direct-save@example.com", "직접저장", cohort=2)
        profile = counselor.counselor_profile
        profile.is_approved = True
        profile.save(update_fields=["is_approved", "updated_at"])
        self.assertFalse(TrainingEnrollment.objects.filter(counselor=counselor).exists())

    def test_list_approval_creates_one_enrollment_per_counselor(self):
        first = _counselor("bulk-one@example.com", "일괄하나", cohort=2)
        second = _counselor("bulk-two@example.com", "일괄둘", cohort=1)
        missing = _counselor("bulk-none@example.com", "일괄무기수")
        url = reverse("admin:accounts_counselorprofile_changelist")
        response = self.http.post(
            url,
            {
                "form-TOTAL_FORMS": "3",
                "form-INITIAL_FORMS": "3",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-id": str(first.counselor_profile.pk),
                "form-0-is_approved": "on",
                "form-1-id": str(second.counselor_profile.pk),
                "form-1-is_approved": "on",
                "form-2-id": str(missing.counselor_profile.pk),
                "form-2-is_approved": "on",
                "_save": "저장",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "상담사 승인 시 기수를 입력해야 합니다.")
        first.counselor_profile.refresh_from_db()
        second.counselor_profile.refresh_from_db()
        missing.counselor_profile.refresh_from_db()
        self.assertFalse(first.counselor_profile.is_approved)
        self.assertFalse(second.counselor_profile.is_approved)
        self.assertFalse(missing.counselor_profile.is_approved)
        self.assertEqual(TrainingEnrollment.objects.count(), 0)

        response = self.http.post(
            url,
            {
                "form-TOTAL_FORMS": "2",
                "form-INITIAL_FORMS": "2",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-id": str(first.counselor_profile.pk),
                "form-0-is_approved": "on",
                "form-1-id": str(second.counselor_profile.pk),
                "form-1-is_approved": "on",
                "_save": "저장",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "일괄하나 상담사의 2기 수련 등록을 만들었습니다.")
        self.assertContains(response, "일괄둘 상담사의 1기 수련 등록을 만들었습니다.")
        self.assertEqual(
            TrainingEnrollment.objects.get(counselor=first).cohort,
            2,
        )
        self.assertEqual(
            TrainingEnrollment.objects.get(counselor=second).status,
            TrainingEnrollmentStatus.ACTIVE,
        )
        self.assertFalse(TrainingEnrollment.objects.filter(counselor=missing).exists())

    def test_user_inline_approval_creates_the_enrollment(self):
        counselor = _counselor("inline-new@example.com", "인라인신규")
        url = reverse("admin:accounts_user_change", args=[counselor.pk])
        post = _admin_post(self.http, url)
        post["counselor_profile-0-cohort"] = "2"
        post["counselor_profile-0-is_approved"] = "on"
        response = self.http.post(url, post, follow=True)

        self.assertEqual(response.status_code, 200, msg=response.content.decode()[:800])
        self.assertContains(response, "인라인신규 상담사의 2기 수련 등록을 만들었습니다.")
        enrollment = TrainingEnrollment.objects.get(counselor=counselor)
        self.assertEqual(enrollment.cohort, 2)
        self.assertEqual(enrollment.status, TrainingEnrollmentStatus.ACTIVE)

    def test_repeat_approval_does_not_duplicate_and_unapproval_keeps_records(self):
        counselor = _counselor("repeat@example.com", "재승인", cohort=1)
        url = reverse("admin:accounts_counselorprofile_change", args=[counselor.counselor_profile.pk])
        post = _admin_post(self.http, url)
        post["cohort"] = "1"
        post["is_approved"] = "on"
        self.http.post(url, post, follow=True)
        enrollment = TrainingEnrollment.objects.get(counselor=counselor)
        client = _client("기존내담")
        application = CounselingApplication.objects.create(
            client=client,
            counseling_types=["개인상담"],
            reason="기존",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.IN_PROGRESS,
        )
        case = Case.objects.create(
            application=application,
            client=client,
            counselor=counselor,
            training_enrollment=enrollment,
            case_number="APPROVAL-1",
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.IN_PERSON,
            total_sessions=8,
            remaining_sessions=8,
        )

        again = _admin_post(self.http, url)
        again["cohort"] = "2"
        again["is_approved"] = "on"
        again["bio"] = "소개만 수정"
        self.http.post(url, again, follow=True)
        counselor.counselor_profile.refresh_from_db()
        self.assertEqual(counselor.counselor_profile.cohort, 2)
        self.assertEqual(self._enrollments(counselor), [enrollment])
        case.refresh_from_db()
        self.assertEqual(case.training_enrollment_id, enrollment.pk)
        self.assertEqual(case.counselor_id, counselor.pk)

        revoked = _admin_post(self.http, url)
        revoked["is_approved"] = ""
        self.http.post(url, revoked, follow=True)
        counselor.counselor_profile.refresh_from_db()
        self.assertFalse(counselor.counselor_profile.is_approved)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(enrollment.cohort, 1)
        case.refresh_from_db()
        self.assertEqual(case.training_enrollment_id, enrollment.pk)

        restored = _admin_post(self.http, url)
        restored["is_approved"] = "on"
        self.http.post(url, restored, follow=True)
        self.assertEqual(TrainingEnrollment.objects.filter(counselor=counselor).count(), 1)
        enrollment.refresh_from_db()
        case.refresh_from_db()
        self.assertEqual(enrollment.status, TrainingEnrollmentStatus.ACTIVE)
        self.assertEqual(case.status, CaseStatus.ACTIVE)
        self.assertEqual(case.training_enrollment_id, enrollment.pk)

    def test_already_approved_profile_without_enrollment_is_not_backfilled_on_save(self):
        counselor = _counselor(
            "legacy-approved@example.com", "기존승인", cohort=2, approved=True
        )
        url = reverse("admin:accounts_counselorprofile_change", args=[counselor.counselor_profile.pk])
        post = _admin_post(self.http, url)
        post["bio"] = "저장만"
        self.http.post(url, post, follow=True)
        self.assertFalse(TrainingEnrollment.objects.filter(counselor=counselor).exists())
        counselor.counselor_profile.refresh_from_db()
        self.assertTrue(counselor.counselor_profile.is_approved)
