"""수퍼비전 회차·출석 권한."""

import re

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


def _attendance_tag(html: str, profile) -> str:
    marker = f'name="attendance_{profile.pk}"'
    start = html.find(marker)
    if start < 0:
        return ""
    return html[start:html.find(">", start)]


class SupervisionAttendanceCheckboxTests(TestCase):
    def setUp(self):
        self.present = _counselor("출석자", 1)
        self.absent = _counselor("결석자", 1)
        self.unchecked = _counselor("미저장", 1)
        self.supervisor = _supervisor("출석수퍼", [1])
        self.other_supervisor = _supervisor("타기수", [2])
        self.admin = _user("출석관리자@example.com", "출석관리자", UserRole.ADMIN)
        self.session = _session(1, 2, SupervisionSessionStatus.COMPLETED)
        SupervisionAttendance.objects.create(
            session=self.session,
            counselor=self.present.counselor_profile,
            status=SupervisionAttendanceStatus.PRESENT,
        )
        SupervisionAttendance.objects.create(
            session=self.session,
            counselor=self.absent.counselor_profile,
            status=SupervisionAttendanceStatus.ABSENT,
        )
        SupervisionAttendance.objects.create(
            session=self.session,
            counselor=self.unchecked.counselor_profile,
            status=SupervisionAttendanceStatus.UNCHECKED,
        )
        self.url = reverse("supervisor:supervision_attendance", args=[self.session.pk])
        self.http = HttpClient()
        self.http.force_login(self.supervisor)

    def test_saved_statuses_render_as_checkboxes_without_writing(self):
        response = self.http.get(self.url)
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "미체크")
        self.assertIsNone(re.search(r">\s*전체 출석", body))
        self.assertIn('aria-label="전체 출석"', body)
        self.assertIn('title="전체 출석"', body)
        self.assertContains(response, "출석 저장")
        self.assertIn("checked", _attendance_tag(body, self.present.counselor_profile))
        self.assertNotIn("checked", _attendance_tag(body, self.absent.counselor_profile))
        self.assertNotIn("checked", _attendance_tag(body, self.unchecked.counselor_profile))
        self.assertEqual(
            SupervisionAttendance.objects.get(
                session=self.session,
                counselor=self.unchecked.counselor_profile,
            ).status,
            SupervisionAttendanceStatus.UNCHECKED,
        )

    def test_save_checks_as_present_and_blanks_as_absent(self):
        extra_present = _counselor("추가출석1", 1)
        extra_present_two = _counselor("추가출석2", 1)
        response = self.http.post(
            self.url,
            {
                f"attendance_{self.present.counselor_profile.pk}": "PRESENT",
                f"attendance_{extra_present.counselor_profile.pk}": "PRESENT",
                f"attendance_{extra_present_two.counselor_profile.pk}": "PRESENT",
            },
        )
        self.assertEqual(response.status_code, 302)

        statuses = {
            row.counselor_id: row.status
            for row in SupervisionAttendance.objects.filter(session=self.session)
        }
        self.assertEqual(statuses[self.present.counselor_profile.pk], SupervisionAttendanceStatus.PRESENT)
        self.assertEqual(statuses[extra_present.counselor_profile.pk], SupervisionAttendanceStatus.PRESENT)
        self.assertEqual(statuses[extra_present_two.counselor_profile.pk], SupervisionAttendanceStatus.PRESENT)
        self.assertEqual(statuses[self.absent.counselor_profile.pk], SupervisionAttendanceStatus.ABSENT)
        self.assertEqual(statuses[self.unchecked.counselor_profile.pk], SupervisionAttendanceStatus.ABSENT)

    def test_clearing_a_present_box_saves_absent_and_checking_absent_saves_present(self):
        response = self.http.post(
            self.url,
            {f"attendance_{self.absent.counselor_profile.pk}": "PRESENT"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            SupervisionAttendance.objects.get(
                session=self.session,
                counselor=self.present.counselor_profile,
            ).status,
            SupervisionAttendanceStatus.ABSENT,
        )
        self.assertEqual(
            SupervisionAttendance.objects.get(
                session=self.session,
                counselor=self.absent.counselor_profile,
            ).status,
            SupervisionAttendanceStatus.PRESENT,
        )

    def test_admin_attendance_page_uses_the_same_checkbox(self):
        http = HttpClient()
        http.force_login(self.admin)
        url = reverse("admin_panel:supervision_attendance", args=[self.session.pk])
        page = http.get(url)
        body = page.content.decode()
        self.assertEqual(page.status_code, 200)
        self.assertIn("checked", _attendance_tag(body, self.present.counselor_profile))
        saved = http.post(
            url,
            {f"attendance_{self.absent.counselor_profile.pk}": "PRESENT"},
        )
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(
            SupervisionAttendance.objects.get(
                session=self.session,
                counselor=self.absent.counselor_profile,
            ).status,
            SupervisionAttendanceStatus.PRESENT,
        )

    def test_other_cohort_supervisor_still_cannot_save(self):
        http = HttpClient()
        http.force_login(self.other_supervisor)
        response = http.post(
            self.url,
            {f"attendance_{self.present.counselor_profile.pk}": "PRESENT"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            SupervisionAttendance.objects.get(
                session=self.session,
                counselor=self.unchecked.counselor_profile,
            ).status,
            SupervisionAttendanceStatus.UNCHECKED,
        )


class AdminSupervisionRosterTests(TestCase):
    def setUp(self):
        self.admin = _user("연번관리@example.com", "연번관리", UserRole.ADMIN)
        self.supervisor = _supervisor("연번수퍼", [4])
        self.session = _session(4, 1, SupervisionSessionStatus.SCHEDULED)
        self.counselors = [
            _counselor(name, 4) for name in ("가상담", "나상담", "다상담")
        ]

    def test_admin_attendance_actions_sit_under_the_roster(self):
        http = HttpClient()
        http.force_login(self.admin)
        response = http.get(
            reverse("admin_panel:supervision_attendance", args=[self.session.pk])
        )
        body = response.content.decode()
        list_url = reverse("admin_panel:supervision_sessions", args=[4])
        edit_url = reverse("admin_panel:supervision_session_edit", args=[self.session.pk])
        above_form = body.split('id="supervision-attendance-form"', 1)[0]
        when = timezone.localtime(self.session.scheduled_at).strftime("%Y-%m-%d %H:%M")
        title = "<h2 class=\"h4 mb-0\">4기 1회차 출석</h2>"
        meta = f"<p class=\"text-muted mb-0\">{when} · 예정</p>"
        self.assertIn("d-flex flex-wrap align-items-center gap-3 mt-4 mb-5", above_form)
        self.assertIn(title, above_form)
        self.assertIn(meta, above_form)
        self.assertLess(above_form.index(title), above_form.index(meta))
        self.assertEqual(body.count(f"{when} · 예정"), 1)
        self.assertNotIn("← 목록으로", above_form)
        self.assertNotIn("회차 수정", above_form)
        self.assertNotIn("history.back", body)
        list_button = f'<a class="btn btn-outline-secondary" href="{list_url}">← 목록으로</a>'
        save_button = '<button type="submit" class="btn btn-primary">출석 저장</button>'
        edit_button = f'<a class="btn btn-outline-primary" href="{edit_url}">회차 수정</a>'
        self.assertIn(list_button, body)
        self.assertIn(save_button, body)
        self.assertIn(edit_button, body)
        self.assertLess(body.index(list_button), body.index(save_button))
        self.assertLess(body.index(save_button), body.index(edit_button))
        self.assertIn(
            "supervision-attendance-actions d-flex flex-wrap align-items-center justify-content-center gap-2 mt-5",
            body,
        )
        self.assertIn("table align-middle mb-0", body)
        self.assertIn("--bs-btn-color: var(--kscu-primary)", body)
        self.assertIn("--bs-btn-border-color: var(--kscu-primary)", body)

    def test_admin_roster_shows_numbers_and_total_without_extra_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        http = HttpClient()
        http.force_login(self.admin)
        url = reverse("admin_panel:supervision_attendance", args=[self.session.pk])
        self.counselors[1].counselor_profile.cohort = 9
        self.counselors[1].counselor_profile.save(update_fields=["cohort"])
        self.counselors[2].counselor_profile.cohort = 9
        self.counselors[2].counselor_profile.save(update_fields=["cohort"])
        with CaptureQueriesContext(connection) as single:
            one = http.get(url)
        self.counselors[1].counselor_profile.cohort = 4
        self.counselors[1].counselor_profile.save(update_fields=["cohort"])
        self.counselors[2].counselor_profile.cohort = 4
        self.counselors[2].counselor_profile.save(update_fields=["cohort"])
        with CaptureQueriesContext(connection) as several:
            page = http.get(url)
        self.assertEqual(one.status_code, 200)
        self.assertEqual(len(single), len(several))
        body = page.content.decode()
        self.assertNotIn("상담사 (총", body)
        self.assertNotIn("총 3명", body)
        self.assertIn(">1<", body)
        self.assertIn(">2<", body)
        self.assertIn(">3<", body)
        self.assertEqual(body.count("text-center align-middle"), 12)
        self.assertIn("<th class=\"text-center align-middle\">번호</th>", body)
        self.assertIn("<th class=\"text-center align-middle\">상담사</th>", body)
        self.assertIn("<span>출석</span>", body)
        self.assertIsNone(re.search(r">\s*전체 출석", body))
        self.assertIn('aria-label="전체 출석"', body)
        self.assertIn('title="전체 출석"', body)
        self.assertIn('id="supervision-attendance-all"', body)
        self.assertIn("box.checked = all.checked", body)
        supervisor = HttpClient()
        supervisor.force_login(self.supervisor)
        supervisor_page = supervisor.get(
            reverse("supervisor:supervision_attendance", args=[self.session.pk])
        )
        supervisor_body = supervisor_page.content.decode()
        when = timezone.localtime(self.session.scheduled_at).strftime("%Y-%m-%d %H:%M")
        list_url = reverse("supervisor:supervision_sessions", args=[4])
        above_form = supervisor_body.split('id="supervision-attendance-form"', 1)[0]
        form = supervisor_body.split('id="supervision-attendance-form"', 1)[1]
        self.assertIn("d-flex flex-wrap align-items-center gap-3 mt-4 mb-5", above_form)
        self.assertIn("<h2 class=\"h4 mb-0\">4기 1회차 출석</h2>", above_form)
        self.assertIn(f"<p class=\"text-muted mb-0\">{when} · 예정</p>", above_form)
        self.assertEqual(supervisor_body.count(f"{when} · 예정"), 1)
        self.assertEqual(form.count("text-center align-middle"), 12)
        self.assertIn(">1<", form)
        self.assertIn(">2<", form)
        self.assertIn(">3<", form)
        self.assertIsNone(re.search(r">\s*전체 출석", supervisor_body))
        self.assertIn('aria-label="전체 출석"', supervisor_body)
        self.assertIn('title="전체 출석"', supervisor_body)
        self.assertIn("box.checked = all.checked", supervisor_body)
        self.assertIn("table align-middle mb-0", form)
        list_button = f'<a class="btn btn-outline-secondary" href="{list_url}">← 목록으로</a>'
        save_button = '<button type="submit" class="btn btn-primary">출석 저장</button>'
        self.assertIn(list_button, form)
        self.assertIn(save_button, form)
        self.assertLess(form.index(list_button), form.index(save_button))
        self.assertIn(
            "supervision-attendance-actions d-flex flex-wrap align-items-center justify-content-center gap-2 mt-5",
            form,
        )
        self.assertNotIn("회차 수정", supervisor_body)
        self.assertNotIn("admin-panel/supervision/sessions/", supervisor_body)
        self.assertNotIn("history.back", supervisor_body)
        self.assertNotIn("총 3명", supervisor_body)

    def test_session_list_offers_a_confirmed_delete(self):
        http = HttpClient()
        http.force_login(self.admin)
        page = http.get(reverse("admin_panel:supervision_sessions", args=[4]))
        delete_url = reverse("admin_panel:supervision_session_delete", args=[self.session.pk])
        self.assertContains(page, "이 수퍼비전 회차를 삭제하시겠습니까? 삭제한 회차는 복구할 수 없습니다.")
        self.assertContains(page, delete_url)
        self.assertContains(page, "4기 1회차")


class AdminSupervisionDeleteTests(TestCase):
    def setUp(self):
        self.admin = _user("삭제관리@example.com", "삭제관리", UserRole.ADMIN)
        self.supervisor = _supervisor("삭제수퍼", [5])
        self.counselor = _counselor("삭제상담", 5)
        self.client_user = _user("삭제내담@example.com", "삭제내담", UserRole.CLIENT)
        self.session = _session(5, 5, SupervisionSessionStatus.SCHEDULED)

    def _delete(self):
        http = HttpClient()
        http.force_login(self.admin)
        return http.post(
            reverse("admin_panel:supervision_session_delete", args=[self.session.pk]),
            follow=True,
        )

    def test_admin_can_delete_a_session_without_attendance(self):
        response = self._delete()
        self.assertContains(response, "수퍼비전 회차를 삭제했습니다.")
        self.assertFalse(SupervisionSession.objects.filter(pk=self.session.pk).exists())
        self.assertEqual(
            response.request["PATH_INFO"],
            reverse("admin_panel:supervision_sessions", args=[5]),
        )

    def test_unchecked_rows_without_confirmation_can_be_deleted(self):
        SupervisionAttendance.objects.create(
            session=self.session,
            counselor=self.counselor.counselor_profile,
            status=SupervisionAttendanceStatus.UNCHECKED,
        )
        response = self._delete()
        self.assertContains(response, "수퍼비전 회차를 삭제했습니다.")
        self.assertFalse(SupervisionSession.objects.filter(pk=self.session.pk).exists())
        self.assertFalse(
            SupervisionAttendance.objects.filter(
                counselor=self.counselor.counselor_profile
            ).exists()
        )

    def test_present_attendance_blocks_deletion(self):
        attendance = SupervisionAttendance.objects.create(
            session=self.session,
            counselor=self.counselor.counselor_profile,
            status=SupervisionAttendanceStatus.PRESENT,
            checked_at=timezone.now(),
        )
        response = self._delete()
        self.assertContains(response, "출석 기록이 있는 수퍼비전 회차는 삭제할 수 없습니다.")
        self.assertTrue(SupervisionSession.objects.filter(pk=self.session.pk).exists())
        attendance.refresh_from_db()
        self.assertEqual(attendance.status, SupervisionAttendanceStatus.PRESENT)

    def test_absent_attendance_blocks_deletion(self):
        SupervisionAttendance.objects.create(
            session=self.session,
            counselor=self.counselor.counselor_profile,
            status=SupervisionAttendanceStatus.ABSENT,
            checked_at=timezone.now(),
        )
        response = self._delete()
        self.assertContains(response, "출석 기록이 있는 수퍼비전 회차는 삭제할 수 없습니다.")
        self.assertTrue(SupervisionSession.objects.filter(pk=self.session.pk).exists())
        self.assertTrue(SupervisionAttendance.objects.filter(session=self.session).exists())

    def test_get_does_not_delete(self):
        http = HttpClient()
        http.force_login(self.admin)
        response = http.get(
            reverse("admin_panel:supervision_session_delete", args=[self.session.pk])
        )
        self.assertEqual(response.status_code, 405)
        self.assertTrue(SupervisionSession.objects.filter(pk=self.session.pk).exists())

    def test_non_admins_cannot_delete(self):
        for user in (self.counselor, self.supervisor, self.client_user):
            http = HttpClient()
            http.force_login(user)
            response = http.post(
                reverse("admin_panel:supervision_session_delete", args=[self.session.pk])
            )
            self.assertEqual(response.status_code, 403)
        self.assertTrue(SupervisionSession.objects.filter(pk=self.session.pk).exists())

    def test_unknown_session_is_404(self):
        import uuid

        http = HttpClient()
        http.force_login(self.admin)
        response = http.post(
            reverse("admin_panel:supervision_session_delete", args=[uuid.uuid4()])
        )
        self.assertEqual(response.status_code, 404)

    def test_cancelled_session_without_recorded_attendance_can_be_deleted(self):
        self.session.status = SupervisionSessionStatus.CANCELLED
        self.session.save(update_fields=["status"])
        response = self._delete()
        self.assertContains(response, "수퍼비전 회차를 삭제했습니다.")
        self.assertFalse(SupervisionSession.objects.filter(pk=self.session.pk).exists())

    def test_completed_session_without_recorded_attendance_can_be_deleted(self):
        self.session.status = SupervisionSessionStatus.COMPLETED
        self.session.save(update_fields=["status"])
        response = self._delete()
        self.assertContains(response, "수퍼비전 회차를 삭제했습니다.")
        self.assertFalse(SupervisionSession.objects.filter(pk=self.session.pk).exists())
