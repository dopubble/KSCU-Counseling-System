"""Case 종결유형 — 저장, admin 정합성, 재배정, 제출 후 운영 상태 수정."""

import json

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied
from django.forms.models import model_to_dict
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.admin import CaseAdmin
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CaseTerminationType,
    CounselingApplication,
    CounselingMethod,
)
from apps.counseling.services import (
    assign_counselor,
    close_case_after_sessions_exhausted,
    close_case_for_early_termination,
    close_case_on_termination_record,
)
from apps.sessions_app.admin import CounselingJournalAdmin
from apps.sessions_app.models import CounselingJournal, TerminationCounselingRecord


def _user(email, name, role):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


def _counselor(name):
    user = _user(f"{name}@example.com", name, UserRole.COUNSELOR)
    CounselorProfile.objects.update_or_create(
        user=user,
        defaults={"cohort": 1, "is_approved": True},
    )
    return user


def _case(counselor, client, number, **kwargs):
    application = CounselingApplication.objects.create(
        client=client,
        counseling_types=["진로상담"],
        reason="test",
        counseling_method=CounselingMethod.IN_PERSON,
        status=ApplicationStatus.IN_PROGRESS,
    )
    return Case.objects.create(
        application=application,
        client=client,
        counselor=counselor,
        case_number=number,
        counseling_method=CounselingMethod.IN_PERSON,
        total_sessions=10,
        remaining_sessions=4,
        **kwargs,
    )


def _form_data(case, **overrides):
    data = model_to_dict(case)
    for key, value in list(data.items()):
        if value is None:
            data[key] = ""
        elif key in ("application", "client", "counselor", "records_submitted_by"):
            data[key] = str(value)
        elif key == "tags":
            data[key] = json.dumps(value)
    data.update(overrides)
    return data


class CaseTerminationTypeModelTests(TestCase):
    def setUp(self):
        self.counselor = _counselor("유형상담")
        self.client_user = _user("유형내담@example.com", "유형내담", UserRole.CLIENT)

    def test_active_case_keeps_blank_termination_type(self):
        case = _case(self.counselor, self.client_user, "CASE-TYPE-A")
        self.assertEqual(case.status, CaseStatus.ACTIVE)
        self.assertEqual(case.termination_type, "")
        self.assertEqual(case.get_status_display(), "개입중")

    def test_closed_normal_and_early_and_blank_save(self):
        normal = _case(
            self.counselor,
            self.client_user,
            "CASE-TYPE-N",
            status=CaseStatus.CLOSED,
            termination_type=CaseTerminationType.NORMAL,
        )
        early_client = _user("조기내담@example.com", "조기내담", UserRole.CLIENT)
        early = _case(
            self.counselor,
            early_client,
            "CASE-TYPE-E",
            status=CaseStatus.CLOSED,
            termination_type=CaseTerminationType.EARLY,
        )
        blank_client = _user("미지정내담@example.com", "미지정내담", UserRole.CLIENT)
        blank = _case(
            self.counselor,
            blank_client,
            "CASE-TYPE-B",
            status=CaseStatus.CLOSED,
            termination_type="",
        )
        normal.refresh_from_db()
        early.refresh_from_db()
        blank.refresh_from_db()
        self.assertEqual(normal.termination_type, CaseTerminationType.NORMAL)
        self.assertEqual(early.termination_type, CaseTerminationType.EARLY)
        self.assertEqual(blank.termination_type, "")

    def test_automatic_close_paths_leave_termination_type_blank(self):
        exhausted = _case(self.counselor, self.client_user, "CASE-TYPE-X")
        close_case_after_sessions_exhausted(exhausted)
        exhausted.refresh_from_db()
        self.assertEqual(exhausted.status, CaseStatus.CLOSED)
        self.assertEqual(exhausted.termination_type, "")

        early_client = _user("당일내담@example.com", "당일내담", UserRole.CLIENT)
        early = _case(self.counselor, early_client, "CASE-TYPE-D")
        close_case_for_early_termination(early)
        early.refresh_from_db()
        self.assertEqual(early.status, CaseStatus.CLOSED)
        self.assertEqual(early.termination_type, "")

        record_client = _user("기록내담유형@example.com", "기록내담유형", UserRole.CLIENT)
        recorded = _case(self.counselor, record_client, "CASE-TYPE-R")
        TerminationCounselingRecord.objects.create(
            case=recorded,
            counselor=self.counselor,
            is_draft=False,
        )
        self.assertTrue(close_case_on_termination_record(recorded))
        recorded.refresh_from_db()
        self.assertEqual(recorded.status, CaseStatus.CLOSED)
        self.assertEqual(recorded.termination_type, "")


class CaseTerminationTypeAdminTests(TestCase):
    def setUp(self):
        self.counselor = _counselor("관리유형상담")
        self.client_user = _user("관리유형내담@example.com", "관리유형내담", UserRole.CLIENT)
        self.case = _case(self.counselor, self.client_user, "CASE-TYPE-ADMIN")
        self.admin_model = CaseAdmin(Case, AdminSite())
        self.superuser = User.objects.create_superuser(
            email="type-admin@example.com",
            password="pass12345",
            name="유형관리자",
        )
        self.request = RequestFactory().post("/admin/")
        self.request.user = self.superuser

    def _save_via_admin(self, case, **overrides):
        form_class = self.admin_model.get_form(self.request, case, change=True)
        form = form_class(data=_form_data(case, **overrides), instance=case)
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.admin_model.save_model(self.request, obj, form, change=True)
        case.refresh_from_db()
        return case

    def test_non_closed_status_clears_termination_type(self):
        saved = self._save_via_admin(
            self.case,
            status=CaseStatus.ON_HOLD,
            termination_type=CaseTerminationType.EARLY,
        )
        self.assertEqual(saved.status, CaseStatus.ON_HOLD)
        self.assertEqual(saved.termination_type, "")

    def test_closed_normal_and_early_are_kept(self):
        normal = self._save_via_admin(
            self.case,
            status=CaseStatus.CLOSED,
            termination_type=CaseTerminationType.NORMAL,
        )
        self.assertEqual(normal.status, CaseStatus.CLOSED)
        self.assertEqual(normal.termination_type, CaseTerminationType.NORMAL)

        early = self._save_via_admin(
            self.case,
            status=CaseStatus.CLOSED,
            termination_type=CaseTerminationType.EARLY,
        )
        self.assertEqual(early.termination_type, CaseTerminationType.EARLY)

    def test_submitted_case_allows_only_status_and_termination_type(self):
        self.case.notes = "기존 메모"
        self.case.remaining_sessions = 4
        self.case.records_submitted_at = timezone.now()
        self.case.records_submitted_by = self.counselor
        self.case.status = CaseStatus.ON_HOLD
        self.case.save()

        self.assertTrue(self.admin_model.has_change_permission(self.request, self.case))
        self.assertFalse(self.admin_model.has_delete_permission(self.request, self.case))
        readonly = self.admin_model.get_readonly_fields(self.request, self.case)
        self.assertNotIn("status", readonly)
        self.assertNotIn("termination_type", readonly)
        self.assertIn("notes", readonly)
        self.assertIn("remaining_sessions", readonly)
        self.assertIn("records_submitted_at", readonly)
        self.assertIn("client", readonly)

        journal = CounselingJournal.objects.create(
            case=self.case,
            counselor=self.counselor,
            session_number=1,
            is_draft=False,
        )
        journal_admin = CounselingJournalAdmin(CounselingJournal, AdminSite())
        self.assertFalse(journal_admin.has_change_permission(self.request, journal))

        saved = self._save_via_admin(
            self.case,
            status=CaseStatus.CLOSED,
            termination_type=CaseTerminationType.EARLY,
        )
        self.assertEqual(saved.status, CaseStatus.CLOSED)
        self.assertEqual(saved.termination_type, CaseTerminationType.EARLY)
        self.assertEqual(saved.notes, "기존 메모")
        self.assertEqual(saved.remaining_sessions, 4)
        self.assertIsNotNone(saved.records_submitted_at)
        self.assertEqual(saved.records_submitted_by_id, self.counselor.pk)

        form_class = self.admin_model.get_form(self.request, saved, change=True)
        form = form_class(
            data=_form_data(
                saved,
                status=CaseStatus.CLOSED,
                termination_type=CaseTerminationType.EARLY,
            ),
            instance=saved,
        )
        self.assertTrue(form.is_valid(), form.errors)
        tampered = form.save(commit=False)
        tampered.notes = "바꿔치기"
        with self.assertRaises(PermissionDenied):
            self.admin_model.save_model(self.request, tampered, form, change=True)
        saved.refresh_from_db()
        self.assertEqual(saved.notes, "기존 메모")
        self.assertEqual(saved.termination_type, CaseTerminationType.EARLY)


class AssignCounselorClearsTerminationTypeTests(TestCase):
    def test_reopening_closed_case_clears_termination_type(self):
        counselor = _counselor("재배정상담")
        client = _user("재배정내담@example.com", "재배정내담", UserRole.CLIENT)
        case = _case(
            counselor,
            client,
            "CASE-TYPE-RE",
            status=CaseStatus.CLOSED,
            termination_type=CaseTerminationType.EARLY,
        )
        case.closed_at = timezone.now()
        case.save(update_fields=["closed_at"])

        assign_counselor(case.application, counselor, total_sessions=10)
        case.refresh_from_db()
        self.assertEqual(case.status, CaseStatus.ACTIVE)
        self.assertEqual(case.termination_type, "")
        self.assertIsNone(case.closed_at)
