from django.test import TestCase

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.forms import CounselingApplyForm
from apps.counseling.models import ApplicationStatus, CounselingApplication, CounselingMethod
from apps.counseling.services import assign_counselor, build_apply_initial_from_application


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

    def test_apply_form_accepts_remote_counseling_method(self):
        form = CounselingApplyForm(
            data={
                "name": "신청자",
                "residence_region": "서울",
                "clinical_diagnosis": "없음",
                "current_medication": "없음",
                "counseling_types": ["진로상담"],
                "counseling_method": CounselingMethod.REMOTE,
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
