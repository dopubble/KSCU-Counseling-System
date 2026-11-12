from datetime import date, time

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.forms import CounselingApplyForm
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CounselingApplication,
    CounselingMethod,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.counseling.constants import APPLICATION_COUNSELING_METHOD_BOTH
from apps.counseling.services import (
    assign_counselor,
    build_apply_initial_from_application,
    sync_case_counseling_method_from_application,
)
from apps.counseling.views import _update_counseling_application


class CounselingMethodApplyTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="client@example.com",
            password="pass12345",
            name="신청자",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.counselor = User.objects.create_user(
            email="counselor@example.com",
            password="pass12345",
            name="상담사",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.get_or_create(user=self.counselor, defaults={"cohort": 1})
        TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
        )

    def test_apply_form_accepts_remote_counseling_method(self):
        form = CounselingApplyForm(
            data={
                "name": "신청자",
                "birth_date": "1990-01-01",
                "residence_region": "서울",
                "clinical_diagnosis": "없음",
                "current_medication": "없음",
                "counseling_types": ["진로상담"],
                "counseling_method": CounselingMethod.REMOTE,
                "weekly_availability": ["TUE:EVENING"],
                "preferred_date": "2026-07-01",
                "preferred_time": "14:00",
                "reason": "스트레스",
            },
            user=self.client_user,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_apply_form_requires_counseling_method_selection(self):
        form = CounselingApplyForm(
            data={
                "name": "신청자",
                "residence_region": "서울",
                "clinical_diagnosis": "없음",
                "current_medication": "없음",
                "counseling_types": ["진로상담"],
                "weekly_availability": ["TUE:EVENING"],
                "preferred_date": "2026-07-01",
                "preferred_time": "14:00",
                "reason": "스트레스",
            },
            user=self.client_user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("counseling_method", form.errors)

    def test_assign_counselor_copies_application_counseling_method_to_case(self):
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["개인상담"],
            reason="비대면 희망",
            counseling_method=CounselingMethod.REMOTE,
            status=ApplicationStatus.WAITING_MATCH,
        )
        case = assign_counselor(application, self.counselor, total_sessions=10)
        self.assertEqual(case.counseling_method, CounselingMethod.REMOTE)

    def test_build_apply_initial_includes_counseling_method(self):
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["개인상담"],
            reason="대면 희망",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.WAITING_MATCH,
        )
        initial = build_apply_initial_from_application(application)
        self.assertEqual(initial["counseling_method"], CounselingMethod.IN_PERSON)

    def test_either_method_is_saved_without_changing_case_method(self):
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["개인상담"],
            reason="비대면 희망",
            counseling_method=CounselingMethod.REMOTE,
            preferred_schedule={
                "preferred_date": "2026-01-01",
                "preferred_time": "09:00",
                "legacy_marker": "keep",
                "available_weekdays": ["SUN", "MON"],
                "available_time_slots": ["EVENING", "MORNING"],
            },
            status=ApplicationStatus.WAITING_MATCH,
        )
        case = assign_counselor(application, self.counselor, total_sessions=10)
        self.assertEqual(case.counseling_method, CounselingMethod.REMOTE)

        _update_counseling_application(
            self.client_user,
            application,
            {
                "name": "신청자",
                "phone": "",
                "birth_date": date(1990, 1, 1),
                "residence_region": "서울",
                "clinical_diagnosis": "없음",
                "current_medication": "없음",
                "occupation": "",
                "counseling_types": ["진로상담"],
                "counseling_method": APPLICATION_COUNSELING_METHOD_BOTH,
                "weekly_availability": {"THU": ["EVENING", "AFTERNOON"]},
                "other_requests": "목요일 오후와 저녁을 선호합니다.",
                "preferred_date": date(2026, 7, 3),
                "preferred_time": time(15, 0),
                "reason": "요일 변경",
            },
        )
        application.refresh_from_db()
        case.refresh_from_db()

        self.assertEqual(application.counseling_method, APPLICATION_COUNSELING_METHOD_BOTH)
        self.assertEqual(application.get_counseling_method_display(), "대면·비대면 모두 가능")
        self.assertEqual(application.preferred_schedule["legacy_marker"], "keep")
        self.assertEqual(application.preferred_schedule["preferred_date"], "2026-01-01")
        self.assertEqual(application.preferred_schedule["preferred_time"], "09:00")
        self.assertEqual(application.preferred_schedule["available_weekdays"], ["SUN", "MON"])
        self.assertEqual(application.preferred_schedule["available_time_slots"], ["EVENING", "MORNING"])
        self.assertEqual(
            application.preferred_schedule["weekly_availability"],
            {"THU": ["AFTERNOON", "EVENING"]},
        )
        self.assertEqual(application.other_requests_text(), "목요일 오후와 저녁을 선호합니다.")
        self.assertEqual(application.available_weekday_label(), "월요일, 일요일")
        self.assertEqual(application.available_time_slot_label(), "오전, 저녁")
        self.assertEqual(application.weekly_availability_lines(), ["목요일: 오후, 저녁"])
        self.assertEqual(case.counseling_method, CounselingMethod.REMOTE)
        sync_case_counseling_method_from_application(application)
        case.refresh_from_db()
        self.assertEqual(case.counseling_method, CounselingMethod.REMOTE)

    def test_both_without_admin_choice_does_not_create_an_in_person_case(self):
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["개인상담"],
            reason="모두 가능",
            counseling_method=APPLICATION_COUNSELING_METHOD_BOTH,
            status=ApplicationStatus.WAITING_MATCH,
        )
        with self.assertRaises(ValueError):
            assign_counselor(application, self.counselor, total_sessions=10)
        self.assertFalse(Case.objects.filter(application=application).exists())

        in_person = assign_counselor(
            application,
            self.counselor,
            total_sessions=10,
            assigned_method=CounselingMethod.IN_PERSON,
        )
        application.refresh_from_db()
        self.assertEqual(in_person.counseling_method, CounselingMethod.IN_PERSON)
        self.assertEqual(application.counseling_method, APPLICATION_COUNSELING_METHOD_BOTH)

    def test_admin_must_choose_method_for_either_preference(self):
        from apps.counseling.models import Case

        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["개인상담"],
            reason="모두 가능",
            counseling_method=APPLICATION_COUNSELING_METHOD_BOTH,
            status=ApplicationStatus.WAITING_MATCH,
        )
        admin = User.objects.create_user(
            email="method-admin@example.com",
            password="pass12345",
            name="배정관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        http = Client()
        url = reverse("counseling:application_detail", args=[application.pk])
        http.force_login(self.client_user)
        denied = http.post(
            url,
            {
                "counselor": str(self.counselor.pk),
                "total_sessions": 10,
                "assigned_method": CounselingMethod.REMOTE,
            },
        )
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(Case.objects.filter(application=application).exists())

        http.force_login(admin)
        page = http.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "실제 상담방식")
        self.assertContains(page, "대면 상담")
        self.assertContains(page, "비대면 상담")
        self.assertNotContains(page, 'value="BOTH"')

        blocked = http.post(
            url,
            {"counselor": str(self.counselor.pk), "total_sessions": 10},
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "실제 상담방식을 선택해 주세요.")
        self.assertFalse(Case.objects.filter(application=application).exists())

        forged = http.post(
            url,
            {
                "counselor": str(self.counselor.pk),
                "total_sessions": 10,
                "assigned_method": APPLICATION_COUNSELING_METHOD_BOTH,
            },
        )
        self.assertEqual(forged.status_code, 200)
        self.assertFalse(Case.objects.filter(application=application).exists())

        chosen = http.post(
            url,
            {
                "counselor": str(self.counselor.pk),
                "total_sessions": 10,
                "assigned_method": CounselingMethod.REMOTE,
            },
        )
        self.assertEqual(chosen.status_code, 302)
        case = Case.objects.get(application=application)
        application.refresh_from_db()
        self.assertEqual(case.counseling_method, CounselingMethod.REMOTE)
        self.assertEqual(application.counseling_method, APPLICATION_COUNSELING_METHOD_BOTH)

    def test_fixed_preference_pages_do_not_ask_for_another_method(self):
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["개인상담"],
            reason="비대면만",
            counseling_method=CounselingMethod.REMOTE,
            status=ApplicationStatus.WAITING_MATCH,
        )
        admin = User.objects.create_user(
            email="fixed-admin@example.com",
            password="pass12345",
            name="고정관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        http = Client()
        http.force_login(admin)
        url = reverse("counseling:application_detail", args=[application.pk])
        page = http.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "실제 상담방식")
        response = http.post(
            url,
            {
                "counselor": str(self.counselor.pk),
                "total_sessions": 8,
                "assigned_method": CounselingMethod.IN_PERSON,
            },
        )
        self.assertEqual(response.status_code, 302)
        case = Case.objects.get(application=application)
        application.refresh_from_db()
        self.assertEqual(case.counseling_method, CounselingMethod.REMOTE)
        self.assertEqual(application.counseling_method, CounselingMethod.REMOTE)
