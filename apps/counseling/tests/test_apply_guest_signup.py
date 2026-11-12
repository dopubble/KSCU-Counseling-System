"""비로그인 상담 신청 시 내담자 가입. 기존 로그인 신청은 그대로 둔다."""

from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.db import IntegrityError
from django.test import Client as HttpClient, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import ClientProfile, User, UserRole, UserStatus
from apps.accounts.email_guard import lock_email_signup
from apps.accounts.forms import SignUpForm
from apps.counseling.apply_registration import (
    EMAIL_AVAILABLE,
    EMAIL_TAKEN,
    email_rate_identity,
)
from apps.counseling.forms import CounselingApplyForm
from apps.counseling.models import (
    ApplicationStatus,
    CounselingApplication,
    CounselingMethod,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.counseling.services import assign_counselor
from apps.documents.models import ConsentDocument
from apps.scheduling.models import Appointment

PASSWORD = "Apply-guest-9"
OTHER_PASSWORD = "Apply-guest-8"


def _guest_data(email="new-client@example.com", **extra):
    data = {
        "name": "신규내담자",
        "email": email,
        "email_confirmed": email,
        "phone": "010-1111-2222",
        "student_id": "20260001",
        "birth_date": "1992-04-05",
        "department": "상담학과",
        "is_kcu_student": "yes",
        "residence_region": "서울",
        "clinical_diagnosis": "없음",
        "current_medication": "없음",
        "occupation": "학생",
        "counseling_types": ["진로상담"],
        "counseling_method": CounselingMethod.IN_PERSON,
        "preferred_date": "2026-08-01",
        "preferred_time": "14:00",
        "reason": "새 상담 신청",
        "password1": PASSWORD,
        "password2": PASSWORD,
        "agree_service_terms": "on",
        "agree_privacy": "on",
        "role": UserRole.ADMIN,
        "is_staff": "1",
        "is_superuser": "1",
    }
    data.update(extra)
    return data


def _user(email, role, name):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


class GuestApplySignupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.http = HttpClient()
        self.url = reverse("counseling:apply")
        self.check_url = reverse("counseling:apply_email_check")

    def test_anonymous_apply_page_shows_signup_fields_only_for_guests(self):
        response = self.http.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "중복확인")
        self.assertContains(response, "비밀번호 확인")
        self.assertContains(response, "숭실사이버대학교 학생 여부")
        self.assertContains(response, "이용약관")
        self.assertContains(response, "개인정보 수집 및 이용")
        self.assertContains(response, "상담 신청하기")
        self.assertContains(response, "/accounts/login/?next=/counseling/apply/")
        self.assertNotContains(response, "id_role")

        client = _user("member@example.com", UserRole.CLIENT, "기존내담자")
        self.http.force_login(client)
        logged_in = self.http.get(self.url)
        self.assertEqual(logged_in.status_code, 200)
        self.assertNotContains(logged_in, "중복확인")
        self.assertNotContains(logged_in, "id_password1")
        self.assertNotContains(logged_in, "id_agree_service_terms")
        self.assertNotContains(logged_in, "id_is_kcu_student_0")
        self.assertContains(logged_in, "이름·학번·소속 학과는 회원가입 정보에서 자동으로 불러오며")
        self.assertContains(logged_in, "신청하기")
        self.assertNotContains(logged_in, "상담 신청하기")

    def test_new_guest_application_creates_client_and_logs_in(self):
        old_client = _user("old-client@example.com", UserRole.CLIENT, "과거내담자")
        old_application = CounselingApplication.objects.create(
            client=old_client,
            counseling_types=["개인상담"],
            reason="기존 신청",
            residence_region="부산",
            clinical_diagnosis="없음",
            current_medication="없음",
            counseling_method=CounselingMethod.REMOTE,
            status=ApplicationStatus.WAITING_MATCH,
            preferred_schedule={"preferred_date": "2026-01-01"},
        )
        with patch("apps.counseling.views.send_new_application_notification") as mail:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.http.post(self.url, _guest_data())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("client:application_list"))
        user = User.objects.get(email="new-client@example.com")
        self.assertEqual(user.role, UserRole.CLIENT)
        self.assertEqual(user.status, UserStatus.ACTIVE)
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.name, "신규내담자")
        self.assertEqual(user.phone, "010-1111-2222")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)
        self.assertNotIn(PASSWORD, user.password)
        profile = user.client_profile
        self.assertEqual(profile.birth_date, date(1992, 4, 5))
        self.assertEqual(profile.student_id, "20260001")
        self.assertEqual(profile.department, "상담학과")
        self.assertTrue(profile.is_kcu_student)
        application = CounselingApplication.objects.get(client=user)
        self.assertEqual(application.status, ApplicationStatus.WAITING_MATCH)
        self.assertEqual(application.reason, "새 상담 신청")
        self.assertEqual(application.client_id, user.id)
        self.assertNotIn("test_submission", application.preferred_schedule)
        self.assertNotIn(PASSWORD, str(application.preferred_schedule))
        self.assertEqual(str(self.http.session.get("_auth_user_id")), str(user.pk))
        self.assertTrue(mail.called)
        self.assertGreaterEqual(len(callbacks), 1)
        mailed = mail.call_args.args[0]
        self.assertEqual(mailed.pk, application.pk)
        self.assertNotIn(PASSWORD, str(mail.call_args))
        follow = self.http.get(response.url)
        self.assertEqual(follow.status_code, 200)
        self.assertContains(follow, "상담 신청 목록")
        detail = self.http.get(
            reverse("client:application_detail", kwargs={"pk": application.pk})
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "새 상담 신청")
        old_application.refresh_from_db()
        self.assertEqual(old_application.client_id, old_client.id)
        self.assertEqual(old_application.reason, "기존 신청")
        self.assertEqual(ConsentDocument.objects.count(), 0)
        self.assertEqual(Appointment.objects.count(), 0)
        self.assertFalse(TrainingEnrollment.objects.filter(counselor=user).exists())

    def test_guest_mail_failure_keeps_the_application_and_login(self):
        with patch(
            "apps.counseling.views.send_new_application_notification",
            side_effect=RuntimeError("smtp down"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.http.post(
                    self.url, _guest_data(email="mail-fail@example.com")
                )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("client:application_list"))
        self.assertTrue(User.objects.filter(email="mail-fail@example.com").exists())
        self.assertEqual(CounselingApplication.objects.count(), 1)

    def test_email_check_available_and_taken_for_every_role(self):
        available = self.http.post(self.check_url, {"email": " Fresh@Example.com "})
        self.assertEqual(available.status_code, 200)
        self.assertEqual(available.json()["message"], EMAIL_AVAILABLE)
        self.assertEqual(available.json()["email"], "Fresh@example.com")

        _user("Client@Example.com", UserRole.CLIENT, "내담자이름")
        _user("counselor@example.com", UserRole.COUNSELOR, "상담사")
        _user("admin@example.com", UserRole.ADMIN, "관리자")
        _user("supervisor@example.com", UserRole.SUPERVISOR, "수퍼바이저")
        for raw in (
            "client@example.com",
            "COUNSELOR@example.com",
            " admin@example.com ",
            "Supervisor@Example.com",
        ):
            taken = self.http.post(self.check_url, {"email": raw})
            self.assertEqual(taken.json()["status"], "taken")
            self.assertEqual(taken.json()["message"], EMAIL_TAKEN)
            self.assertIn("/accounts/login/?next=/counseling/apply/", taken.json()["login_url"])
            self.assertNotContains(taken, "내담자이름")
            self.assertNotContains(taken, "상담사")
            self.assertNotContains(taken, "관리자")

        invalid = self.http.post(self.check_url, {"email": "not-an-email"})
        self.assertEqual(invalid.json()["status"], "invalid")
        client = _user("logged-in@example.com", UserRole.CLIENT, "로그인")
        self.http.force_login(client)
        denied = self.http.post(self.check_url, {"email": "another@example.com"})
        self.assertEqual(denied.status_code, 403)

    def test_guest_submit_rechecks_email_and_rejects_bypass(self):
        _user("Taken@Example.com", UserRole.CLIENT, "이미가입")
        bypass = self.http.post(
            self.url,
            _guest_data(email="open@example.com", email_confirmed=""),
        )
        self.assertEqual(bypass.status_code, 200)
        self.assertContains(bypass, "이메일 중복확인을 완료해 주세요.")
        self.assertFalse(User.objects.filter(email="open@example.com").exists())

        changed = self.http.post(
            self.url,
            _guest_data(
                email="second@example.com",
                email_confirmed="first@example.com",
            ),
        )
        self.assertEqual(changed.status_code, 200)
        self.assertFalse(User.objects.filter(email="second@example.com").exists())

        taken = self.http.post(
            self.url,
            _guest_data(email="taken@example.com", email_confirmed="taken@example.com"),
        )
        self.assertContains(taken, EMAIL_TAKEN)
        self.assertContains(taken, "로그인하기")
        self.assertEqual(User.objects.filter(email__iexact="taken@example.com").count(), 1)
        self.assertEqual(CounselingApplication.objects.count(), 0)

    def test_case_difference_and_second_submit_do_not_create_another_account(self):
        first = self.http.post(self.url, _guest_data(email="Case@Example.com"))
        self.assertEqual(first.status_code, 302)
        other = HttpClient()
        second = other.post(
            self.url,
            _guest_data(email="case@example.com", email_confirmed="case@example.com"),
        )
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, EMAIL_TAKEN)
        self.assertEqual(User.objects.filter(email__iexact="case@example.com").count(), 1)

    def test_integrity_error_and_profile_or_application_failure_roll_back(self):
        with patch(
            "apps.counseling.apply_registration.User.objects.create_user",
            side_effect=IntegrityError("FOREIGN KEY constraint failed"),
        ):
            failed = self.http.post(self.url, _guest_data(email="race@example.com"))
        self.assertContains(failed, "상담 신청을 접수하지 못했습니다.")
        self.assertNotContains(failed, EMAIL_TAKEN)
        self.assertNotContains(failed, "FOREIGN KEY")
        self.assertEqual(CounselingApplication.objects.count(), 0)
        self.assertFalse(User.objects.filter(email="race@example.com").exists())

        with patch(
            "apps.counseling.apply_registration.ClientProfile.objects.create",
            side_effect=RuntimeError("profile failed"),
        ):
            profile_failed = self.http.post(
                self.url, _guest_data(email="profile@example.com")
            )
        self.assertContains(profile_failed, "상담 신청을 접수하지 못했습니다.")
        self.assertFalse(User.objects.filter(email="profile@example.com").exists())
        self.assertEqual(ClientProfile.objects.count(), 0)
        self.assertEqual(CounselingApplication.objects.count(), 0)

        with patch(
            "apps.counseling.views.CounselingApplication.objects.create",
            side_effect=RuntimeError("application failed"),
        ):
            application_failed = self.http.post(
                self.url, _guest_data(email="application@example.com")
            )
        self.assertContains(application_failed, "상담 신청을 접수하지 못했습니다.")
        self.assertFalse(User.objects.filter(email="application@example.com").exists())
        self.assertEqual(ClientProfile.objects.count(), 0)
        self.assertEqual(CounselingApplication.objects.count(), 0)

    def test_invalid_password_consent_and_required_fields_create_nothing(self):
        mismatch = self.http.post(
            self.url,
            _guest_data(email="mismatch@example.com", password2=OTHER_PASSWORD),
        )
        self.assertContains(mismatch, "비밀번호가 일치하지 않습니다.")
        weak = self.http.post(
            self.url,
            _guest_data(email="weak@example.com", password1="12345678", password2="12345678"),
        )
        self.assertEqual(weak.status_code, 200)
        self.assertFalse(User.objects.filter(email="weak@example.com").exists())
        missing_terms = self.http.post(
            self.url, _guest_data(email="terms@example.com", agree_service_terms="")
        )
        self.assertContains(missing_terms, "이용약관에 동의해 주세요.")
        missing_privacy = self.http.post(
            self.url, _guest_data(email="privacy@example.com", agree_privacy="")
        )
        self.assertContains(missing_privacy, "개인정보 수집 및 이용에 동의해 주세요.")
        missing_region = dict(_guest_data(email="region@example.com"))
        missing_region.pop("residence_region")
        missing = self.http.post(self.url, missing_region)
        self.assertEqual(missing.status_code, 200)
        self.assertFalse(User.objects.filter(email="region@example.com").exists())
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(CounselingApplication.objects.count(), 0)

    def test_csrf_is_required_for_guest_post(self):
        guarded = HttpClient(enforce_csrf_checks=True)
        response = guarded.post(self.url, _guest_data(email="csrf@example.com"))
        self.assertEqual(response.status_code, 403)
        check = guarded.post(self.check_url, {"email": "csrf@example.com"})
        self.assertEqual(check.status_code, 403)
        self.assertFalse(User.objects.filter(email="csrf@example.com").exists())

    @override_settings(
        APPLY_GUEST_EMAIL_CHECK_IP_LIMIT=2,
        APPLY_GUEST_EMAIL_CHECK_WINDOW=600,
        APPLY_GUEST_SUBMIT_ADDRESS_LIMIT=1,
        APPLY_GUEST_SUBMIT_WINDOW=600,
    )
    def test_email_check_and_submit_are_rate_limited_in_the_process_cache(self):
        self.assertEqual(self.http.post(self.check_url, {"email": "a@example.com"}).status_code, 200)
        self.assertEqual(self.http.post(self.check_url, {"email": "b@example.com"}).status_code, 200)
        limited = self.http.post(self.check_url, {"email": "c@example.com"})
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["message"], "잠시 후 다시 확인해 주세요.")

        first = self.http.post(self.url, _guest_data(email="limit@example.com", name=""))
        self.assertEqual(first.status_code, 200)
        second = self.http.post(self.url, _guest_data(email="limit@example.com"))
        self.assertEqual(second.status_code, 429)
        self.assertFalse(User.objects.filter(email="limit@example.com").exists())

    def test_postgres_lock_uses_the_same_key_for_different_email_case(self):
        with patch("apps.accounts.email_guard.connection") as conn:
            conn.vendor = "postgresql"
            cursor = conn.cursor.return_value.__enter__.return_value
            lock_email_signup("Foo@Example.com")
            lock_email_signup("foo@example.com")
        first = cursor.execute.call_args_list[0].args[1]
        second = cursor.execute.call_args_list[1].args[1]
        self.assertEqual(first, second)

    def test_student_and_non_student_profiles_follow_signup_rules(self):
        student = self.http.post(
            self.url,
            _guest_data(
                email="student-apply@example.com",
                is_kcu_student="yes",
                department="상담심리학과",
                student_id="20261234",
            ),
        )
        self.assertEqual(student.status_code, 302)
        student_profile = User.objects.get(email="student-apply@example.com").client_profile
        self.assertTrue(student_profile.is_kcu_student)
        self.assertEqual(student_profile.department, "상담심리학과")
        self.assertEqual(student_profile.student_id, "20261234")

        missing = HttpClient().post(
            self.url,
            _guest_data(
                email="student-nodept@example.com",
                is_kcu_student="yes",
                department="",
            ),
        )
        self.assertContains(missing, "소속 학과를 입력해 주세요.")
        self.assertFalse(User.objects.filter(email="student-nodept@example.com").exists())

        other = HttpClient()
        visitor = other.post(
            self.url,
            _guest_data(
                email="visitor-apply@example.com",
                is_kcu_student="no",
                department="저장되면안됨",
                student_id="20119999",
            ),
        )
        self.assertEqual(visitor.status_code, 302)
        visitor_profile = User.objects.get(email="visitor-apply@example.com").client_profile
        self.assertFalse(visitor_profile.is_kcu_student)
        self.assertEqual(visitor_profile.department, "")
        self.assertEqual(visitor_profile.student_id, "20119999")

    def test_signup_and_apply_block_the_same_email_regardless_of_case(self):
        signup = SignUpForm(
            data={
                "email": "Shared@Example.com",
                "name": "가입자",
                "phone": "010-1234-5678",
                "role": UserRole.CLIENT,
                "password1": "Testpass123!",
                "password2": "Testpass123!",
                "agree_service_terms": True,
                "agree_privacy": True,
                "birth_date": "1991-02-03",
                "is_kcu_student": "yes",
                "department": "상담심리학과",
                "student_id": "",
            }
        )
        self.assertTrue(signup.is_valid(), signup.errors)
        created = signup.save()
        self.assertTrue(created.client_profile.is_kcu_student)
        self.assertEqual(created.client_profile.department, "상담심리학과")
        blocked = self.http.post(
            self.url,
            _guest_data(email="shared@example.com", email_confirmed="shared@example.com"),
        )
        self.assertContains(blocked, EMAIL_TAKEN)
        self.assertEqual(User.objects.filter(email__iexact="shared@example.com").count(), 1)

        applied = HttpClient().post(
            self.url,
            _guest_data(
                email="FromApply@Example.com",
                is_kcu_student="no",
                department="무시됨",
                student_id="",
            ),
        )
        self.assertEqual(applied.status_code, 302)
        later_signup = SignUpForm(
            data={
                "email": "fromapply@example.com",
                "name": "나중가입",
                "phone": "010-1234-5678",
                "role": UserRole.CLIENT,
                "password1": "Testpass123!",
                "password2": "Testpass123!",
                "agree_service_terms": True,
                "agree_privacy": True,
                "birth_date": "1991-02-03",
                "is_kcu_student": "no",
            }
        )
        self.assertFalse(later_signup.is_valid())
        self.assertEqual(later_signup.errors["email"], ["이미 가입된 이메일입니다."])

    def test_rate_limit_key_does_not_contain_the_email(self):
        identity = email_rate_identity(" Person@Example.com ")
        self.assertNotIn("person", identity)
        self.assertNotIn("@", identity)
        self.assertEqual(identity, email_rate_identity("person@example.com"))

    def test_existing_client_apply_keeps_account_and_blocks_a_second_pending_application(self):
        client = _user("keep@example.com", UserRole.CLIENT, "유지내담자")
        ClientProfile.objects.create(
            user=client,
            birth_date=date(1985, 1, 2),
            is_kcu_student=True,
            student_id="20110009",
            department="기존학과",
        )
        self.http.force_login(client)
        with patch("apps.counseling.views.send_new_application_notification") as mail:
            response = self.http.post(
                self.url,
                {
                    "phone": "010-3333-4444",
                    "residence_region": "서울",
                    "clinical_diagnosis": "없음",
                    "current_medication": "없음",
                    "counseling_types": ["진로상담"],
                    "counseling_method": CounselingMethod.IN_PERSON,
                    "preferred_date": "2026-09-01",
                    "preferred_time": "10:00",
                    "reason": "기존 회원 신청",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("client:application_list"))
        self.assertTrue(mail.called)
        client.refresh_from_db()
        self.assertEqual(client.name, "유지내담자")
        self.assertEqual(client.email, "keep@example.com")
        self.assertEqual(client.phone, "010-3333-4444")
        self.assertEqual(client.role, UserRole.CLIENT)
        profile = client.client_profile
        profile.refresh_from_db()
        self.assertEqual(profile.department, "기존학과")
        self.assertEqual(profile.student_id, "20110009")
        self.assertTrue(profile.is_kcu_student)
        self.assertEqual(profile.birth_date, date(1985, 1, 2))
        application = CounselingApplication.objects.get(client=client)
        self.assertEqual(application.reason, "기존 회원 신청")
        self.assertEqual(User.objects.count(), 1)

        again = self.http.post(
            self.url,
            {
                "phone": "010-3333-4444",
                "residence_region": "서울",
                "clinical_diagnosis": "없음",
                "current_medication": "없음",
                "counseling_types": ["진로상담"],
                "counseling_method": CounselingMethod.IN_PERSON,
                "preferred_date": "2026-09-02",
                "preferred_time": "11:00",
                "reason": "중복 신청",
            },
        )
        self.assertEqual(again.status_code, 302)
        self.assertEqual(CounselingApplication.objects.filter(client=client).count(), 1)
        self.assertFalse(
            CounselingApplication.objects.filter(reason="중복 신청").exists()
        )

    def test_counselor_supervisor_and_admin_permissions_stay_the_same(self):
        counselor = _user("staff-counselor@example.com", UserRole.COUNSELOR, "상담사")
        supervisor = _user("staff-supervisor@example.com", UserRole.SUPERVISOR, "수퍼바이저")
        admin = _user("staff-admin@example.com", UserRole.ADMIN, "관리자")
        for user in (counselor, supervisor):
            self.http.force_login(user)
            response = self.http.post(
                self.url,
                {
                    "birth_date": "1990-01-01",
                    "residence_region": "서울",
                    "clinical_diagnosis": "없음",
                    "current_medication": "없음",
                    "counseling_types": ["진로상담"],
                    "counseling_method": CounselingMethod.IN_PERSON,
                    "preferred_date": "2026-09-01",
                    "preferred_time": "10:00",
                    "reason": "권한 없는 신청",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "내담자(학생) 계정 또는 관리자(테스트)")
        self.assertFalse(CounselingApplication.objects.exists())

        self.http.force_login(admin)
        response = self.http.post(
            self.url,
            {
                "birth_date": "1990-01-01",
                "residence_region": "서울",
                "clinical_diagnosis": "없음",
                "current_medication": "없음",
                "counseling_types": ["진로상담"],
                "counseling_method": CounselingMethod.REMOTE,
                "preferred_date": "2026-09-01",
                "preferred_time": "10:00",
                "reason": "관리자 테스트",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin_panel:dashboard"))
        application = CounselingApplication.objects.get()
        self.assertEqual(application.client_id, admin.id)
        self.assertTrue(application.preferred_schedule.get("test_submission"))
        self.assertEqual(User.objects.count(), 3)

    def test_new_application_is_visible_to_admin_and_can_be_matched(self):
        self.http.post(self.url, _guest_data(email="match-me@example.com"))
        application = CounselingApplication.objects.get()
        counselor = _user("match-counselor@example.com", UserRole.COUNSELOR, "배정상담사")
        from apps.accounts.models import CounselorProfile

        CounselorProfile.objects.get_or_create(user=counselor, defaults={"cohort": 1})
        TrainingEnrollment.objects.create(
            counselor=counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        case = assign_counselor(application, counselor, total_sessions=8)
        application.refresh_from_db()
        self.assertEqual(case.client_id, application.client_id)
        self.assertEqual(application.status, ApplicationStatus.IN_PROGRESS)
        self.assertEqual(case.counseling_method, CounselingMethod.IN_PERSON)

        admin = _user("list-admin@example.com", UserRole.ADMIN, "목록관리자")
        self.http.force_login(admin)
        listing = self.http.get(reverse("admin_panel:matching_list"))
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "신규내담자")

        anonymous = HttpClient()
        hidden = anonymous.get(
            reverse("client:application_detail", kwargs={"pk": application.pk})
        )
        self.assertEqual(hidden.status_code, 302)
        self.assertIn("/accounts/login/", hidden.url)
        edit = anonymous.post(
            reverse("client:edit_application", kwargs={"pk": application.pk})
        )
        self.assertEqual(edit.status_code, 302)
        admin_page = anonymous.get(reverse("admin_panel:matching_list"))
        self.assertEqual(admin_page.status_code, 302)

        other = _user("other-client@example.com", UserRole.CLIENT, "다른내담자")
        other_http = HttpClient()
        other_http.force_login(other)
        denied = other_http.get(
            reverse("client:application_detail", kwargs={"pk": application.pk})
        )
        self.assertEqual(denied.status_code, 404)
        self.http.force_login(other)
        forbidden = self.http.get(reverse("admin_panel:matching_list"))
        self.assertEqual(forbidden.status_code, 403)
        case_page = other_http.get(reverse("client:case_detail", kwargs={"pk": case.pk}))
        self.assertEqual(case_page.status_code, 404)

    def test_guest_form_rejects_unconfirmed_email_without_using_anonymous_user(self):
        form = CounselingApplyForm(data=_guest_data(email_confirmed=""), user=AnonymousUser())
        self.assertFalse(form.is_valid())
        self.assertIn("이메일 중복확인을 완료해 주세요.", form.errors["email"])
