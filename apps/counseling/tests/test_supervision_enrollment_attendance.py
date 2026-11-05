"""수퍼비전 출석 명단은 회차 기수의 Enrollment를 따른다."""

from datetime import datetime

from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CounselorProfile, SupervisorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    CounselingMethod,
    SupervisionAttendance,
    SupervisionAttendanceStatus,
    SupervisionSession,
    SupervisionSessionStatus,
    TrainingCertificateApproval,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.counseling.training_certificate import (
    STATUS_APPROVED,
    build_counselor_training_certificate_context,
    calculate_training_certificate_hash,
    get_training_certificate_approval_status,
)
from apps.counseling.training_progress import get_counselor_training_progress


def _attendance_tag(html: str, profile) -> str:
    marker = f'name="attendance_{profile.pk}"'
    start = html.find(marker)
    if start < 0:
        return ""
    return html[start:html.find(">", start)]


def _user(email, name, role):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


def _counselor(name, cohort):
    user = _user(f"{name}@supervision.example.com", name, UserRole.COUNSELOR)
    CounselorProfile.objects.update_or_create(
        user=user,
        defaults={"cohort": cohort, "is_approved": True},
    )
    return user


def _supervisor(name, cohorts):
    user = _user(f"{name}@supervision.example.com", name, UserRole.SUPERVISOR)
    SupervisorProfile.objects.update_or_create(
        user=user,
        defaults={"assigned_cohorts": cohorts},
    )
    return user


def _session(cohort, number, *, minutes, status=SupervisionSessionStatus.COMPLETED):
    return SupervisionSession.objects.create(
        cohort=cohort,
        session_number=number,
        title=f"{number}회",
        scheduled_at=timezone.make_aware(datetime(2026, 3, number, 10, 0)),
        duration_minutes=minutes,
        counseling_method=CounselingMethod.REMOTE,
        status=status,
    )


def _enrollment(counselor, cohort, status):
    return TrainingEnrollment.objects.create(
        counselor=counselor,
        cohort=cohort,
        status=status,
    )


class SupervisionEnrollmentAttendanceTests(TestCase):
    def setUp(self):
        self.counselor = _counselor("홍길동", 2)
        self.first = _enrollment(self.counselor, 1, TrainingEnrollmentStatus.COMPLETED)
        self.second = _enrollment(self.counselor, 2, TrainingEnrollmentStatus.ACTIVE)
        self.only_first = _counselor("일만상담", 1)
        _enrollment(self.only_first, 1, TrainingEnrollmentStatus.ACTIVE)
        self.profile_only = _counselor("프로필상담", 2)
        _enrollment(self.profile_only, 1, TrainingEnrollmentStatus.COMPLETED)
        self.withdrawn = _counselor("중단상담", 1)
        _enrollment(self.withdrawn, 1, TrainingEnrollmentStatus.WITHDRAWN)
        self.legacy = _counselor("단기수상담", 1)
        self.past = _session(1, 1, minutes=50)
        self.past_attendance = SupervisionAttendance.objects.create(
            session=self.past,
            counselor=self.counselor.counselor_profile,
            status=SupervisionAttendanceStatus.PRESENT,
            checked_at=timezone.now(),
        )
        self.new_second = _session(2, 2, minutes=80)
        self.new_first = _session(1, 3, minutes=40)
        self.admin = _user("출석관리@supervision.example.com", "출석관리", UserRole.ADMIN)
        self.supervisor_x = _supervisor("김교수", [1])
        self.supervisor_y = _supervisor("이교수", [2])
        self.past_url = reverse("admin_panel:supervision_attendance", args=[self.past.pk])
        self.second_url = reverse("admin_panel:supervision_attendance", args=[self.new_second.pk])
        self.first_url = reverse("admin_panel:supervision_attendance", args=[self.new_first.pk])

    def _admin(self):
        http = HttpClient()
        http.force_login(self.admin)
        return http

    def test_past_attendance_stays_visible_after_profile_moves(self):
        before = self.past_attendance.pk
        page = self._admin().get(self.past_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "홍길동")
        self.assertContains(page, "checked")
        self.past_attendance.refresh_from_db()
        self.assertEqual(self.past_attendance.pk, before)
        self.assertEqual(self.past_attendance.session_id, self.past.pk)
        self.assertEqual(self.past_attendance.status, SupervisionAttendanceStatus.PRESENT)
        self.assertEqual(
            SupervisionAttendance.objects.filter(session=self.past).count(),
            1,
        )

    def test_unchanged_save_keeps_present_and_absent_row_ids(self):
        absent = SupervisionAttendance.objects.create(
            session=self.past,
            counselor=self.profile_only.counselor_profile,
            status=SupervisionAttendanceStatus.ABSENT,
            checked_at=timezone.now(),
        )
        present_id = self.past_attendance.pk
        absent_id = absent.pk
        http = self._admin()
        page = http.get(self.past_url)
        body = page.content.decode()
        self.assertEqual(page.status_code, 200)
        self.assertIn("checked", _attendance_tag(body, self.counselor.counselor_profile))
        self.assertNotIn("checked", _attendance_tag(body, self.profile_only.counselor_profile))
        self.assertEqual(SupervisionAttendance.objects.filter(session=self.past).count(), 2)

        saved = http.post(
            self.past_url,
            {f"attendance_{self.counselor.counselor_profile.pk}": "PRESENT"},
        )
        self.assertEqual(saved.status_code, 302)
        self.past_attendance.refresh_from_db()
        absent.refresh_from_db()
        self.assertEqual(self.past_attendance.pk, present_id)
        self.assertEqual(absent.pk, absent_id)
        self.assertEqual(self.past_attendance.status, SupervisionAttendanceStatus.PRESENT)
        self.assertEqual(absent.status, SupervisionAttendanceStatus.ABSENT)
        self.assertEqual(self.past_attendance.session_id, self.past.pk)
        self.assertEqual(absent.session_id, self.past.pk)

    def test_resaving_the_session_keeps_existing_rows_and_blocks_cohort_move(self):
        http = self._admin()
        edit_url = reverse("admin_panel:supervision_session_edit", args=[self.past.pk])
        kept = http.post(
            edit_url,
            {
                "cohort": 1,
                "session_number": 1,
                "title": "1회",
                "scheduled_at": "2026-03-01T10:00",
                "duration_minutes": 50,
                "counseling_method": CounselingMethod.REMOTE,
                "status": SupervisionSessionStatus.COMPLETED,
            },
        )
        self.assertEqual(kept.status_code, 302)
        self.past_attendance.refresh_from_db()
        self.past.refresh_from_db()
        self.assertEqual(self.past.cohort, 1)
        self.assertEqual(self.past_attendance.session_id, self.past.pk)
        self.assertEqual(self.past_attendance.status, SupervisionAttendanceStatus.PRESENT)
        self.assertEqual(SupervisionAttendance.objects.filter(session=self.past).count(), 1)

        moved = http.post(
            edit_url,
            {
                "cohort": 2,
                "session_number": 1,
                "title": "1회",
                "scheduled_at": "2026-03-01T10:00",
                "duration_minutes": 50,
                "counseling_method": CounselingMethod.REMOTE,
                "status": SupervisionSessionStatus.COMPLETED,
            },
        )
        self.assertEqual(moved.status_code, 200)
        self.assertContains(moved, "출석 기록이 있는 회차의 기수는 바꿀 수 없습니다.")
        self.past.refresh_from_db()
        self.past_attendance.refresh_from_db()
        self.assertEqual(self.past.cohort, 1)
        self.assertEqual(self.past_attendance.session_id, self.past.pk)
        self.assertEqual(self.past_attendance.status, SupervisionAttendanceStatus.PRESENT)

        payload = {
            "cohort": 2,
            "session_number": 1,
            "title": "1회",
            "scheduled_at": "2026-03-01T10:00",
            "duration_minutes": 50,
            "counseling_method": CounselingMethod.REMOTE,
            "status": SupervisionSessionStatus.COMPLETED,
        }
        for supervisor in (self.supervisor_x, self.supervisor_y):
            supervisor_http = HttpClient()
            supervisor_http.force_login(supervisor)
            denied = supervisor_http.post(edit_url, payload)
            self.assertEqual(denied.status_code, 403)
            self.past.refresh_from_db()
            self.assertEqual(self.past.cohort, 1)

    def test_new_roster_uses_active_enrollment_for_that_cohort(self):
        http = self._admin()
        second = http.get(self.second_url)
        self.assertContains(second, "홍길동")
        self.assertNotContains(second, "일만상담")
        self.assertNotContains(second, "프로필상담")
        self.assertNotContains(second, "중단상담")
        self.assertNotContains(second, "단기수상담")
        self.assertFalse(SupervisionAttendance.objects.filter(session=self.new_second).exists())

        first = http.get(self.first_url)
        self.assertNotContains(first, "홍길동")
        self.assertContains(first, "일만상담")
        self.assertNotContains(first, "프로필상담")
        self.assertNotContains(first, "중단상담")
        self.assertFalse(
            SupervisionAttendance.objects.filter(
                session=self.new_first,
                counselor=self.counselor.counselor_profile,
            ).exists()
        )

        saved = http.post(
            self.second_url,
            {f"attendance_{self.counselor.counselor_profile.pk}": "PRESENT"},
        )
        self.assertEqual(saved.status_code, 302)
        created = SupervisionAttendance.objects.get(session=self.new_second)
        self.assertEqual(created.counselor_id, self.counselor.counselor_profile.pk)
        self.assertEqual(created.status, SupervisionAttendanceStatus.PRESENT)
        self.assertEqual(SupervisionAttendance.objects.filter(session=self.new_second).count(), 1)
        again = http.post(
            self.second_url,
            {f"attendance_{self.counselor.counselor_profile.pk}": "PRESENT"},
        )
        self.assertEqual(again.status_code, 302)
        self.assertEqual(SupervisionAttendance.objects.filter(session=self.new_second).count(), 1)

        injected = http.post(
            self.second_url,
            {
                f"attendance_{self.counselor.counselor_profile.pk}": "PRESENT",
                f"attendance_{self.profile_only.counselor_profile.pk}": "PRESENT",
                f"attendance_{self.only_first.counselor_profile.pk}": "PRESENT",
            },
        )
        self.assertEqual(injected.status_code, 302)
        self.assertFalse(
            SupervisionAttendance.objects.filter(
                session=self.new_second,
                counselor=self.profile_only.counselor_profile,
            ).exists()
        )
        self.assertFalse(
            SupervisionAttendance.objects.filter(
                session=self.new_second,
                counselor=self.only_first.counselor_profile,
            ).exists()
        )
        self.past_attendance.refresh_from_db()
        self.assertEqual(self.past_attendance.status, SupervisionAttendanceStatus.PRESENT)
        self.assertEqual(self.past_attendance.session_id, self.past.pk)

    def test_attendance_minutes_stay_on_the_session_cohort(self):
        context = build_counselor_training_certificate_context(self.counselor, self.first)
        content_hash = calculate_training_certificate_hash(self.counselor, context)
        approval = TrainingCertificateApproval.objects.create(
            counselor=self.counselor,
            training_enrollment=self.first,
            approved_by=self.admin,
            approved_role=UserRole.ADMIN,
            content_hash=content_hash,
        )
        approved_at = approval.approved_at
        approval_id = approval.pk
        self.assertEqual(
            get_training_certificate_approval_status(
                self.counselor, training_enrollment=self.first
            )["status"],
            STATUS_APPROVED,
        )

        SupervisionAttendance.objects.create(
            session=self.new_second,
            counselor=self.counselor.counselor_profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )
        first = get_counselor_training_progress(self.counselor, self.first)
        second = get_counselor_training_progress(self.counselor, self.second)
        self.assertEqual(first["supervision_attended_minutes"], 50)
        self.assertEqual(second["supervision_attended_minutes"], 80)
        approval.refresh_from_db()
        self.assertEqual(approval.pk, approval_id)
        self.assertEqual(approval.content_hash, content_hash)
        self.assertEqual(approval.approved_at, approved_at)
        self.assertEqual(approval.approved_by_id, self.admin.pk)
        self.assertEqual(approval.training_enrollment_id, self.first.pk)
        again = get_training_certificate_approval_status(
            self.counselor, training_enrollment=self.first
        )
        second_status = get_training_certificate_approval_status(
            self.counselor, training_enrollment=self.second
        )
        self.assertEqual(again["status"], STATUS_APPROVED)
        self.assertEqual(again["content_hash"], content_hash)
        self.assertNotEqual(second_status["status"], STATUS_APPROVED)

    def test_supervisors_cannot_open_the_other_cohort(self):
        first_supervisor = reverse("supervisor:supervision_attendance", args=[self.past.pk])
        second_supervisor = reverse(
            "supervisor:supervision_attendance",
            args=[self.new_second.pk],
        )
        x_http = HttpClient()
        x_http.force_login(self.supervisor_x)
        self.assertEqual(x_http.get(first_supervisor).status_code, 200)
        self.assertEqual(x_http.get(second_supervisor).status_code, 403)
        self.assertEqual(
            x_http.post(
                second_supervisor,
                {f"attendance_{self.counselor.counselor_profile.pk}": "PRESENT"},
            ).status_code,
            403,
        )
        y_http = HttpClient()
        y_http.force_login(self.supervisor_y)
        self.assertEqual(y_http.get(second_supervisor).status_code, 200)
        self.assertEqual(y_http.get(first_supervisor).status_code, 403)
        self.assertEqual(
            y_http.post(
                first_supervisor,
                {f"attendance_{self.counselor.counselor_profile.pk}": "PRESENT"},
            ).status_code,
            403,
        )
        self.past_attendance.refresh_from_db()
        self.assertEqual(self.past_attendance.status, SupervisionAttendanceStatus.PRESENT)
        self.assertFalse(SupervisionAttendance.objects.filter(session=self.new_second).exists())

    def test_legacy_counselor_without_enrollment_keeps_profile_cohort(self):
        http = self._admin()
        page = http.get(self.past_url)
        self.assertContains(page, "단기수상담")
        self.assertNotContains(http.get(self.second_url), "단기수상담")
        before = SupervisionAttendance.objects.filter(session=self.past).count()
        saved = http.post(
            self.past_url,
            {
                f"attendance_{self.counselor.counselor_profile.pk}": "PRESENT",
                f"attendance_{self.legacy.counselor_profile.pk}": "PRESENT",
            },
        )
        self.assertEqual(saved.status_code, 302)
        legacy_row = SupervisionAttendance.objects.get(
            session=self.past,
            counselor=self.legacy.counselor_profile,
        )
        self.assertEqual(legacy_row.status, SupervisionAttendanceStatus.PRESENT)
        self.past_attendance.refresh_from_db()
        self.assertEqual(self.past_attendance.status, SupervisionAttendanceStatus.PRESENT)
        self.assertEqual(self.past_attendance.session_id, self.past.pk)
        self.assertGreater(SupervisionAttendance.objects.filter(session=self.past).count(), before)
        self.assertFalse(
            SupervisionAttendance.objects.filter(
                session=self.new_second,
                counselor=self.legacy.counselor_profile,
            ).exists()
        )
