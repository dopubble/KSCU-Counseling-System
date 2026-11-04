"""새 Case와 Approval은 현재 ACTIVE TrainingEnrollment에 연결한다."""

import json
from unittest.mock import MagicMock, patch

from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied
from django.forms.models import model_to_dict
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.counseling.admin import CaseAdminForm

from apps.accounts.models import User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CounselingApplication,
    CounselingMethod,
    TrainingCertificateApproval,
    TrainingCertificateApprovalRevocation,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.counseling.services import assign_counselor, reassign_counselor
from apps.counseling.training_certificate import (
    approve_training_certificate,
    build_counselor_training_certificate_context,
    build_training_certificate_approval_payload,
    calculate_training_certificate_hash,
    revoke_training_certificate,
)
from apps.counseling.training_enrollment import (
    ACTIVE_ENROLLMENT_AMBIGUOUS,
    ACTIVE_ENROLLMENT_MISSING,
    ActiveTrainingEnrollmentError,
    get_active_training_enrollment,
)


def _admin_form_data(case, **overrides):
    data = model_to_dict(case)
    for key, value in list(data.items()):
        if value is None:
            data[key] = ""
        elif hasattr(value, "hex"):
            data[key] = str(value)
        elif key == "tags":
            data[key] = json.dumps(value)
    data.update(overrides)
    return data


def _user(email, name, role):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


def _counselor(name, cohort):
    user = _user(f"{name}@write.example.com", name, UserRole.COUNSELOR)
    profile = user.counselor_profile
    profile.cohort = cohort
    profile.is_approved = True
    profile.save(update_fields=["cohort", "is_approved", "updated_at"])
    return user


def _enrollment(counselor, cohort, status=TrainingEnrollmentStatus.ACTIVE):
    return TrainingEnrollment.objects.create(
        counselor=counselor,
        cohort=cohort,
        status=status,
    )


def _client(name):
    return _user(f"{name}@write-client.example.com", name, UserRole.CLIENT)


def _application(client):
    return CounselingApplication.objects.create(
        client=client,
        counseling_types=["진로상담"],
        reason="배정",
        counseling_method=CounselingMethod.IN_PERSON,
        status=ApplicationStatus.WAITING_MATCH,
    )


def _admin():
    return _user("배정관리@write.example.com", "배정관리", UserRole.ADMIN)


class TrainingEnrollmentWritePathTests(TestCase):
    def test_new_case_links_the_single_active_enrollment(self):
        counselor = _counselor("배정상담", 1)
        enrollment = _enrollment(counselor, 1)
        client = _client("배정내담")
        application = _application(client)
        http = Client()
        http.force_login(_admin())

        response = http.post(
            reverse("counseling:application_detail", args=[application.pk]),
            {"counselor": str(counselor.pk), "total_sessions": 10},
        )

        self.assertEqual(response.status_code, 302)
        case = Case.objects.get(application=application)
        self.assertEqual(case.counselor_id, counselor.pk)
        self.assertEqual(case.training_enrollment_id, enrollment.pk)

    def test_assignment_without_active_enrollment_is_rejected(self):
        counselor = _counselor("무기수", None)
        client = _client("무기수내담")
        application = _application(client)
        http = Client()
        http.force_login(_admin())

        response = http.post(
            reverse("counseling:application_detail", args=[application.pk]),
            {"counselor": str(counselor.pk), "total_sessions": 10},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            ACTIVE_ENROLLMENT_MISSING,
            [message.message for message in get_messages(response.wsgi_request)],
        )
        self.assertFalse(Case.objects.filter(application=application).exists())
        application.refresh_from_db()
        self.assertEqual(application.status, ApplicationStatus.WAITING_MATCH)

    def test_existing_case_keeps_enrollment_when_profile_cohort_changes(self):
        counselor = _counselor("기수변경", 1)
        first = _enrollment(counselor, 1)
        client = _client("기존내담")
        application = _application(client)
        case = assign_counselor(application, counselor, total_sessions=10)
        self.assertEqual(case.training_enrollment_id, first.pk)

        profile = counselor.counselor_profile
        profile.cohort = 2
        profile.save(update_fields=["cohort", "updated_at"])
        case.refresh_from_db()

        self.assertEqual(case.training_enrollment_id, first.pk)
        self.assertEqual(counselor.counselor_profile.cohort, 2)

    def test_new_case_uses_the_current_active_enrollment(self):
        counselor = _counselor("이기관", 1)
        first = _enrollment(counselor, 1)
        first.status = TrainingEnrollmentStatus.COMPLETED
        first.save(update_fields=["status"])
        second = _enrollment(counselor, 2)
        client = _client("이期内담")
        application = _application(client)

        case = assign_counselor(application, counselor, total_sessions=10)

        self.assertEqual(case.training_enrollment_id, second.pk)
        self.assertNotEqual(case.training_enrollment_id, first.pk)

    def test_reassign_moves_the_case_to_the_new_counselor_enrollment(self):
        counselor_a = _counselor("상담A", 1)
        counselor_b = _counselor("상담B", 2)
        enrollment_a = _enrollment(counselor_a, 1)
        enrollment_b = _enrollment(counselor_b, 2)
        client = _client("재배정내담")
        case = assign_counselor(_application(client), counselor_a, total_sessions=10)

        reassign_counselor(case, counselor_b)
        case.refresh_from_db()

        self.assertEqual(case.counselor_id, counselor_b.pk)
        self.assertEqual(case.training_enrollment_id, enrollment_b.pk)
        self.assertNotEqual(case.training_enrollment_id, enrollment_a.pk)

    def test_submitted_case_cannot_be_reassigned(self):
        counselor_a = _counselor("잠금A", 1)
        counselor_b = _counselor("잠금B", 2)
        enrollment_a = _enrollment(counselor_a, 1)
        _enrollment(counselor_b, 2)
        case = assign_counselor(_application(_client("잠금내담")), counselor_a, total_sessions=10)
        case.records_submitted_at = timezone.now()
        case.save(update_fields=["records_submitted_at"])

        with self.assertRaises(PermissionDenied):
            reassign_counselor(case, counselor_b)
        case.refresh_from_db()
        self.assertEqual(case.counselor_id, counselor_a.pk)
        self.assertEqual(case.training_enrollment_id, enrollment_a.pk)

    def test_new_approval_links_the_active_enrollment(self):
        counselor = _counselor("승인상담", 1)
        enrollment = _enrollment(counselor, 1)
        approver = _admin()
        case = assign_counselor(_application(_client("승인내담")), counselor, total_sessions=10)
        case.records_submitted_at = timezone.now()
        case.save(update_fields=["records_submitted_at"])
        before = build_counselor_training_certificate_context(counselor)
        before_payload = build_training_certificate_approval_payload(counselor, before)
        scoped = build_counselor_training_certificate_context(counselor, enrollment)

        approval = approve_training_certificate(counselor, approver)

        self.assertEqual(approval.training_enrollment_id, enrollment.pk)
        self.assertEqual(
            approval.content_hash,
            calculate_training_certificate_hash(counselor, scoped),
        )
        self.assertEqual(approval.approved_by_id, approver.pk)
        after = build_counselor_training_certificate_context(counselor)
        after_payload = build_training_certificate_approval_payload(counselor, after)
        self.assertEqual(after_payload, before_payload)
        self.assertEqual(before_payload["schema"], "certificate-metrics-v2")
        scoped_payload = build_training_certificate_approval_payload(counselor, scoped)
        self.assertEqual(scoped_payload["schema"], "certificate-metrics-v3")
        self.assertEqual(scoped_payload["training_enrollment_id"], str(enrollment.pk))

    def test_reapproval_links_the_same_active_enrollment_and_keeps_history(self):
        counselor = _counselor("재승인상담", 4)
        enrollment = _enrollment(counselor, 4)
        approver = _admin()
        case = assign_counselor(_application(_client("재승인내담")), counselor, total_sessions=10)
        case.records_submitted_at = timezone.now()
        case.save(update_fields=["records_submitted_at"])
        original = approve_training_certificate(counselor, approver)
        original_hash = original.content_hash
        original_approved_at = original.approved_at
        revocation = revoke_training_certificate(counselor, approver, reason="확인")

        renewed = approve_training_certificate(counselor, approver)
        original.refresh_from_db()
        revocation.refresh_from_db()

        self.assertNotEqual(renewed.pk, original.pk)
        self.assertEqual(renewed.training_enrollment_id, enrollment.pk)
        self.assertEqual(original.content_hash, original_hash)
        self.assertEqual(original.approved_by_id, approver.pk)
        self.assertEqual(original.approved_role, UserRole.ADMIN)
        self.assertEqual(original.approved_at, original_approved_at)
        self.assertEqual(revocation.approval_id, original.pk)
        self.assertEqual(revocation.revoked_by_id, approver.pk)
        self.assertEqual(revocation.reason, "확인")
        self.assertEqual(TrainingCertificateApproval.objects.filter(counselor=counselor).count(), 2)

    def test_repeated_approval_does_not_rewrite_the_existing_row(self):
        counselor = _counselor("유지승인", 6)
        enrollment = _enrollment(counselor, 6)
        approver = _admin()
        case = assign_counselor(_application(_client("유지내담")), counselor, total_sessions=10)
        case.records_submitted_at = timezone.now()
        case.save(update_fields=["records_submitted_at"])
        original = approve_training_certificate(counselor, approver)

        again = approve_training_certificate(counselor, approver)
        original.refresh_from_db()

        self.assertEqual(again.pk, original.pk)
        self.assertEqual(original.training_enrollment_id, enrollment.pk)
        self.assertEqual(original.content_hash, again.content_hash)
        self.assertFalse(
            TrainingCertificateApprovalRevocation.objects.filter(approval=original).exists()
        )

    def test_helper_rejects_more_than_one_active_enrollment(self):
        counselor = _counselor("중복활성", 1)
        first = _enrollment(counselor, 1)
        second = TrainingEnrollment(counselor=counselor, cohort=2, status=TrainingEnrollmentStatus.ACTIVE)
        query = MagicMock()
        query.order_by.return_value.__getitem__.return_value = [first, second]
        with patch(
            "apps.counseling.training_enrollment.TrainingEnrollment.objects.filter",
            return_value=query,
        ):
            with self.assertRaises(ActiveTrainingEnrollmentError) as caught:
                get_active_training_enrollment(counselor)
        self.assertEqual(str(caught.exception), ACTIVE_ENROLLMENT_AMBIGUOUS)

    def test_completed_enrollment_can_be_approved_without_an_active_row(self):
        counselor = _counselor("수료승인", 3)
        admin = _admin()
        enrollment = _enrollment(counselor, 3)
        case = assign_counselor(_application(_client("수료내담")), counselor, total_sessions=10)
        case.records_submitted_at = timezone.now()
        case.save(update_fields=["records_submitted_at"])
        enrollment.status = TrainingEnrollmentStatus.COMPLETED
        enrollment.save(update_fields=["status"])
        existing = TrainingCertificateApproval.objects.create(
            counselor=counselor,
            training_enrollment=enrollment,
            content_hash="c" * 64,
            approved_by=admin,
            approved_role=UserRole.ADMIN,
        )
        snapshot = (
            existing.pk,
            existing.content_hash,
            existing.approved_by_id,
            existing.approved_role,
            existing.approved_at,
            existing.training_enrollment_id,
        )
        http = Client()
        http.force_login(admin)

        response = http.post(
            reverse(
                "admin_panel:counselor_training_certificate_approve",
                kwargs={"counselor_id": counselor.pk},
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            "수련확인증을 승인했습니다.",
            [message.message for message in get_messages(response.wsgi_request)],
        )
        existing.refresh_from_db()
        self.assertEqual(
            (
                existing.pk,
                existing.content_hash,
                existing.approved_by_id,
                existing.approved_role,
                existing.approved_at,
                existing.training_enrollment_id,
            ),
            snapshot,
        )
        created = TrainingCertificateApproval.objects.exclude(pk=existing.pk).get()
        self.assertEqual(created.training_enrollment_id, enrollment.pk)
        self.assertNotEqual(created.content_hash, existing.content_hash)
        self.assertFalse(
            TrainingCertificateApprovalRevocation.objects.filter(approval=existing).exists()
        )

    def test_admin_edit_without_counselor_change_keeps_training_enrollment(self):
        counselor = _counselor("관리수정", 1)
        first = _enrollment(counselor, 1, status=TrainingEnrollmentStatus.COMPLETED)
        second = _enrollment(counselor, 2)
        client = _client("관리내담")
        application = _application(client)
        case = Case.objects.create(
            application=application,
            client=client,
            counselor=counselor,
            training_enrollment=first,
            case_number="CASE-ADMIN-KEEP",
        )
        form = CaseAdminForm(
            data=_admin_form_data(
                case,
                notes="상담사와 무관한 메모",
                training_enrollment=str(second.pk),
            ),
            instance=case,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()

        self.assertEqual(saved.notes, "상담사와 무관한 메모")
        self.assertEqual(saved.counselor_id, counselor.pk)
        self.assertEqual(saved.training_enrollment_id, first.pk)
        self.assertNotEqual(saved.training_enrollment_id, second.pk)

    def test_admin_counselor_change_uses_the_new_active_enrollment(self):
        counselor_a = _counselor("관리A", 1)
        counselor_b = _counselor("관리B", 2)
        first = _enrollment(counselor_a, 1)
        second = _enrollment(counselor_b, 2)
        client = _client("관리변경내담")
        application = _application(client)
        case = Case.objects.create(
            application=application,
            client=client,
            counselor=counselor_a,
            training_enrollment=first,
            case_number="CASE-ADMIN-MOVE",
        )
        form = CaseAdminForm(
            data=_admin_form_data(case, counselor=str(counselor_b.pk)),
            instance=case,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.counselor_id, counselor_b.pk)
        self.assertEqual(saved.training_enrollment_id, second.pk)

        counselor_c = _counselor("관리C", None)
        refused = CaseAdminForm(
            data=_admin_form_data(saved, counselor=str(counselor_c.pk)),
            instance=saved,
        )
        self.assertFalse(refused.is_valid())
        self.assertIn(ACTIVE_ENROLLMENT_MISSING, refused.errors["counselor"])
        saved.refresh_from_db()
        self.assertEqual(saved.counselor_id, counselor_b.pk)
        self.assertEqual(saved.training_enrollment_id, second.pk)

    def test_admin_new_case_links_the_active_enrollment(self):
        counselor = _counselor("관리생성", 5)
        enrollment = _enrollment(counselor, 5)
        client = _client("관리생성내담")
        application = _application(client)
        draft = Case(
            application=application,
            client=client,
            counselor=counselor,
            status="ACTIVE",
        )
        form = CaseAdminForm(
            data=_admin_form_data(
                draft,
                case_number="CASE-ADMIN-NEW",
                training_enrollment="",
            ),
            instance=draft,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.counselor_id, counselor.pk)
        self.assertEqual(saved.training_enrollment_id, enrollment.pk)
