"""수퍼비전 회차·출석 권한."""

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CounselorProfile, SupervisorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    SupervisionAttendance,
    SupervisionAttendanceStatus,
    SupervisionSession,
    SupervisionSessionStatus,
)
from apps.counseling.supervision_attendance import assert_cohort_access


def _user(email, name, role):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


def _counselor(name, cohort):
    user = _user(f"{name}@example.com", name, UserRole.COUNSELOR)
    CounselorProfile.objects.update_or_create(
        user=user,
        defaults={"cohort": cohort, "is_approved": True},
    )
    return user


def _supervisor(name, cohorts):
    user = _user(f"{name}@example.com", name, UserRole.SUPERVISOR)
    SupervisorProfile.objects.update_or_create(
        user=user,
        defaults={"assigned_cohorts": cohorts},
    )
    return user


def _session(cohort, number, status):
    return SupervisionSession.objects.create(
        cohort=cohort,
        session_number=number,
        scheduled_at=timezone.now(),
        status=status,
        title=f"{number}회",
    )


class SupervisionAttendanceAccessTests(TestCase):
    def setUp(self):
        self.counselor = _counselor("출석상담", 1)
        self.supervisor = _supervisor("담당수퍼", [1])
        self.other_supervisor = _supervisor("다른수퍼", [2])
        self.admin = _user("출석관리@example.com", "출석관리", UserRole.ADMIN)
        self.session = _session(1, 1, SupervisionSessionStatus.COMPLETED)
        self.other_session = _session(2, 1, SupervisionSessionStatus.COMPLETED)

    def test_supervisor_cannot_open_another_cohort(self):
        http = HttpClient()
        http.force_login(self.supervisor)
        response = http.get(
            reverse("supervisor:supervision_attendance", args=[self.other_session.pk])
        )
        self.assertEqual(response.status_code, 403)
        with self.assertRaises(PermissionDenied):
            assert_cohort_access(self.supervisor, 2)

    def test_supervisor_can_save_attendance_for_assigned_cohort(self):
        http = HttpClient()
        http.force_login(self.supervisor)
        cohorts = http.get(reverse("supervisor:supervision_cohorts"))
        self.assertEqual(cohorts.status_code, 200)
        self.assertContains(cohorts, "1기")
        sessions = http.get(reverse("supervisor:supervision_sessions", args=[1]))
        self.assertEqual(sessions.status_code, 200)
        url = reverse("supervisor:supervision_attendance", args=[self.session.pk])
        detail = http.get(url)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "출석상담")
        response = http.post(
            url,
            {f"attendance_{self.counselor.counselor_profile.pk}": "PRESENT"},
        )
        self.assertEqual(response.status_code, 302)
        attendance = SupervisionAttendance.objects.get(
            session=self.session,
            counselor=self.counselor.counselor_profile,
        )
        self.assertEqual(attendance.status, SupervisionAttendanceStatus.PRESENT)
        self.assertEqual(attendance.checked_by, self.supervisor)

    def test_duplicate_attendance_is_rejected(self):
        SupervisionAttendance.objects.create(
            session=self.session,
            counselor=self.counselor.counselor_profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SupervisionAttendance.objects.create(
                    session=self.session,
                    counselor=self.counselor.counselor_profile,
                    status=SupervisionAttendanceStatus.ABSENT,
                )

    def test_admin_can_create_session_and_supervisor_cannot_open_admin_page(self):
        admin_http = HttpClient()
        admin_http.force_login(self.admin)
        response = admin_http.post(
            reverse("admin_panel:supervision_session_create", args=[1]),
            {
                "cohort": 1,
                "session_number": 5,
                "title": "추가회차",
                "scheduled_at": "2026-11-01T10:00",
                "duration_minutes": 50,
                "status": SupervisionSessionStatus.SCHEDULED,
            },
        )
        self.assertEqual(response.status_code, 302)
        created = SupervisionSession.objects.get(cohort=1, session_number=5)
        self.assertEqual(created.status, SupervisionSessionStatus.SCHEDULED)

        supervisor_http = HttpClient()
        supervisor_http.force_login(self.other_supervisor)
        denied = supervisor_http.get(reverse("admin_panel:supervision_cohorts"))
        self.assertEqual(denied.status_code, 403)

        admin_list = admin_http.get(reverse("admin_panel:supervision_sessions", args=[2]))
        self.assertEqual(admin_list.status_code, 200)
        self.assertContains(admin_list, "1회")
