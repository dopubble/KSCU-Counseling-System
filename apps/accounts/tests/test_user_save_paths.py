"""기존 계정 저장과 회원가입 요청 제한.

SQLite 에서 실행한다. PostgreSQL 동시 가입은 여기서 검증하지 않는다.
"""

from datetime import date
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import update_last_login
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.forms import ValidationError
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.admin import UserAdmin
from apps.accounts.email_guard import (
    EmailAlreadyRegistered,
    is_email_unique_violation,
    lock_email_signup,
)
from apps.accounts.forms import (
    KoreanPasswordChangeForm,
    KoreanSetPasswordForm,
    SignUpForm,
)
from apps.accounts.models import (
    ClientProfile,
    CounselorProfile,
    User,
    UserRole,
    UserStatus,
)
from apps.counseling.apply_registration import (
    client_ip,
    create_client_account,
    guest_request_is_limited,
)
from apps.counseling.models import ApplicationStatus, CounselingApplication

PASSWORD = "Testpass123!"


def _session_request(path="/admin/accounts/user/"):
    request = RequestFactory().post(path)
    SessionMiddleware(lambda req: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)
    return request


class ExistingAccountSaveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="member@example.com",
            password=PASSWORD,
            name="기존회원",
            phone="010-0000-0000",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.profile = ClientProfile.objects.create(
            user=self.user,
            birth_date=date(1991, 2, 3),
            is_kcu_student=False,
        )
        self.other = User.objects.create_user(
            email="other@example.com",
            password=PASSWORD,
            name="다른회원",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )

    def test_loaded_save_does_not_lock_or_reread_user(self):
        user = User.objects.get(pk=self.user.pk)
        with patch("apps.accounts.email_guard.lock_email_signup") as locked:
            with CaptureQueriesContext(connection) as captured:
                user.phone = "010-2222-3333"
                user.save()
        self.assertFalse(locked.called)
        user_selects = [
            query["sql"]
            for query in captured.captured_queries
            if query["sql"].startswith("SELECT") and "accounts_user" in query["sql"]
        ]
        self.assertEqual(user_selects, [])
        user.refresh_from_db()
        self.assertEqual(user.phone, "010-2222-3333")
        self.assertEqual(user.email, "member@example.com")

    def test_phone_update_fields_skip_email_check(self):
        user = User.objects.get(pk=self.user.pk)
        with patch("apps.accounts.email_guard.email_is_registered") as checked:
            user.phone = "010-3333-4444"
            user.save(update_fields=["phone", "updated_at"])
        self.assertFalse(checked.called)

    def test_own_email_is_not_a_duplicate(self):
        user = User.objects.get(pk=self.user.pk)
        user.email = "Member@example.com"
        user.save()
        self.assertEqual(User.objects.filter(email__iexact="member@example.com").count(), 1)
        user.refresh_from_db()
        self.assertEqual(user.email, "Member@example.com")

    def test_email_change_to_existing_address_keeps_old_email(self):
        user = User.objects.get(pk=self.user.pk)
        user.email = "OTHER@example.com"
        with self.assertRaises(EmailAlreadyRegistered):
            user.save()
        user.refresh_from_db()
        self.assertEqual(user.email, "member@example.com")
        self.assertTrue(User.objects.filter(email="other@example.com").exists())

    def test_profile_update_changes_phone_only(self):
        client = Client()
        client.force_login(self.user)
        with patch("apps.accounts.email_guard.lock_email_signup") as locked:
            response = client.post(
                reverse("accounts:profile_update"),
                {
                    "phone": "010-7777-8888",
                    "birth_date": "1991-02-03",
                    "is_kcu_student": "no",
                    "email": "someone-else@example.com",
                },
            )
        self.assertRedirects(response, reverse("accounts:profile_update"))
        self.assertFalse(locked.called)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "member@example.com")
        self.assertEqual(self.user.phone, "010-0000-0000")

        response = client.post(
            reverse("accounts:profile_update"),
            {
                "phone": "010-7777-8888",
                "birth_date": "1991-02-03",
                "is_kcu_student": "no",
            },
        )
        self.assertRedirects(response, reverse("client:dashboard"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, "010-7777-8888")
        self.assertEqual(self.user.email, "member@example.com")

    def test_password_change_and_reset_do_not_lock_email(self):
        user = User.objects.get(pk=self.user.pk)
        change = KoreanPasswordChangeForm(
            user,
            {
                "old_password": PASSWORD,
                "new_password1": "Newpass1234!",
                "new_password2": "Newpass1234!",
            },
        )
        self.assertTrue(change.is_valid(), change.errors)
        with patch("apps.accounts.email_guard.lock_email_signup") as locked:
            change.save()
        self.assertFalse(locked.called)
        self.assertTrue(user.check_password("Newpass1234!"))

        reset = KoreanSetPasswordForm(
            user,
            {"new_password1": "Resetpass1234!", "new_password2": "Resetpass1234!"},
        )
        self.assertTrue(reset.is_valid(), reset.errors)
        with patch("apps.accounts.email_guard.lock_email_signup") as locked:
            reset.save()
        self.assertFalse(locked.called)
        user.refresh_from_db()
        self.assertTrue(user.check_password("Resetpass1234!"))
        self.assertEqual(user.email, "member@example.com")

    def test_login_last_login_update_does_not_lock_email(self):
        user = User.objects.get(pk=self.user.pk)
        with patch("apps.accounts.email_guard.lock_email_signup") as locked:
            update_last_login(sender=User, user=user)
        self.assertFalse(locked.called)
        user.refresh_from_db()
        self.assertIsNotNone(user.last_login)

    def test_counselor_approval_and_status_change_keep_email(self):
        counselor = User.objects.create_user(
            email="counselor@example.com",
            password=PASSWORD,
            name="상담사",
            role=UserRole.COUNSELOR,
            status=UserStatus.PENDING,
        )
        profile = counselor.counselor_profile
        profile.cohort = 3
        profile.is_approved = False
        profile.save(update_fields=["cohort", "is_approved", "updated_at"])
        loaded = User.objects.get(pk=counselor.pk)
        with patch("apps.accounts.email_guard.lock_email_signup") as locked:
            profile.is_approved = True
            profile.save(update_fields=["is_approved", "updated_at"])
            loaded.status = UserStatus.ACTIVE
            loaded.save()
        self.assertFalse(locked.called)
        loaded.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(loaded.status, UserStatus.ACTIVE)
        self.assertTrue(loaded.is_active)
        self.assertTrue(profile.is_approved)
        self.assertEqual(loaded.email, "counselor@example.com")

    def test_admin_form_rejects_another_users_email_and_keeps_own(self):
        request = _session_request()
        admin_form = UserAdmin(User, AdminSite()).get_form(request, self.user)
        own = admin_form(instance=self.user)
        own.cleaned_data = {"email": "MEMBER@example.com"}
        self.assertEqual(own.clean_email(), "MEMBER@example.com")

        taken = admin_form(instance=self.user)
        taken.cleaned_data = {"email": "Other@example.com"}
        with self.assertRaises(ValidationError):
            taken.clean_email()

    def test_admin_duplicate_save_redirects_instead_of_500(self):
        request = _session_request()
        admin_obj = UserAdmin(User, AdminSite())
        with patch(
            "django.contrib.auth.admin.UserAdmin.changeform_view",
            side_effect=EmailAlreadyRegistered(),
        ):
            response = admin_obj.changeform_view(request, str(self.user.pk))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, request.path)

    def test_signup_and_apply_share_case_insensitive_block(self):
        data = {
            "email": "Shared@example.com",
            "name": "가입자",
            "phone": "010-1234-5678",
            "role": UserRole.CLIENT,
            "password1": PASSWORD,
            "password2": PASSWORD,
            "agree_service_terms": True,
            "agree_privacy": True,
            "birth_date": "1990-01-01",
            "is_kcu_student": "no",
        }
        form = SignUpForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        with self.assertRaises(EmailAlreadyRegistered):
            create_client_account(
                {
                    "email": "shared@example.com",
                    "password": PASSWORD,
                    "name": "신청자",
                    "phone": "010-9999-0000",
                    "birth_date": date(1992, 4, 5),
                    "is_kcu_student": True,
                    "department": "상담학과",
                }
            )
        self.assertEqual(User.objects.filter(email__iexact="shared@example.com").count(), 1)

    def test_failed_create_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                User.objects.create_user(
                    email="rollback@example.com",
                    password=PASSWORD,
                    name="롤백",
                    role=UserRole.CLIENT,
                    status=UserStatus.ACTIVE,
                )
                raise RuntimeError("저장 중단")
        self.assertFalse(User.objects.filter(email="rollback@example.com").exists())

    def test_sqlite_lock_is_a_no_op(self):
        self.assertNotEqual(connection.vendor, "postgresql")
        with CaptureQueriesContext(connection) as captured:
            lock_email_signup("member@example.com")
        self.assertEqual(captured.captured_queries, [])

    def test_existing_application_is_not_rewritten_by_signup(self):
        application = CounselingApplication.objects.create(
            client=self.user,
            reason="기존 상담기록",
            status=ApplicationStatus.WAITING_MATCH,
        )
        data = {
            "email": "another@example.com",
            "name": "새회원",
            "phone": "010-1234-5678",
            "role": UserRole.CLIENT,
            "password1": PASSWORD,
            "password2": PASSWORD,
            "agree_service_terms": True,
            "agree_privacy": True,
            "birth_date": "1990-01-01",
            "is_kcu_student": "no",
        }
        form = SignUpForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        application.refresh_from_db()
        self.assertEqual(application.reason, "기존 상담기록")
        self.assertEqual(application.client_id, self.user.pk)
        self.assertEqual(application.status, ApplicationStatus.WAITING_MATCH)

    def test_email_unique_violation_is_duplicate_and_rolls_back(self):
        with patch(
            "django.db.models.base.Model.save",
            side_effect=IntegrityError(
                'duplicate key value violates unique constraint "accounts_user_email_key"'
            ),
        ):
            with self.assertRaises(EmailAlreadyRegistered):
                User.objects.create_user(
                    email="dup-constraint@example.com",
                    password=PASSWORD,
                    name="중복",
                    role=UserRole.CLIENT,
                    status=UserStatus.ACTIVE,
                )
        self.assertFalse(User.objects.filter(email="dup-constraint@example.com").exists())

    def test_other_integrity_error_is_not_reported_as_duplicate_email(self):
        with patch(
            "django.db.models.base.Model.save",
            side_effect=IntegrityError(
                "UNIQUE constraint failed: accounts_counselorprofile.user_id"
            ),
        ):
            with self.assertRaises(IntegrityError):
                User.objects.create_user(
                    email="other-constraint@example.com",
                    password=PASSWORD,
                    name="다른제약",
                    role=UserRole.CLIENT,
                    status=UserStatus.ACTIVE,
                )
        self.assertFalse(
            User.objects.filter(email="other-constraint@example.com").exists()
        )

    def test_constraint_name_on_database_error_identifies_email_only(self):
        class Diag:
            constraint_name = "accounts_user_email_key"

        class DatabaseError(Exception):
            def __init__(self):
                super().__init__("constraint violation")
                self.diag = Diag()

        wrapped = IntegrityError("database error")
        wrapped.__cause__ = DatabaseError()
        self.assertTrue(is_email_unique_violation(wrapped))

        other = IntegrityError("UNIQUE constraint failed: accounts_counselorprofile.user_id")
        self.assertFalse(is_email_unique_violation(other))


class SignupRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def _signup(self, email, role=UserRole.CLIENT):
        return {
            "email": email,
            "name": "신청자",
            "phone": "010-1234-5678",
            "role": role,
            "password1": PASSWORD,
            "password2": PASSWORD,
            "agree_service_terms": "on",
            "agree_privacy": "on",
            "birth_date": "1990-01-01",
            "is_kcu_student": "no",
        }

    @override_settings(SIGNUP_ADDRESS_LIMIT=1, SIGNUP_WINDOW=600)
    def test_second_signup_post_is_429_and_creates_one_user(self):
        client = Client()
        url = reverse("accounts:signup")
        first = client.post(url, self._signup("limit@example.com"))
        self.assertEqual(first.status_code, 302)
        second = Client().post(url, self._signup("limit@example.com"))
        self.assertEqual(second.status_code, 429)
        self.assertContains(second, "요청이 많습니다", status_code=429)
        self.assertEqual(User.objects.filter(email="limit@example.com").count(), 1)

    def test_counselor_signup_stays_pending_and_logged_out(self):
        client = Client()
        response = client.post(
            reverse("accounts:signup"),
            self._signup("counselor-new@example.com", role=UserRole.COUNSELOR),
        )
        self.assertRedirects(response, reverse("accounts:login"))
        user = User.objects.get(email="counselor-new@example.com")
        self.assertEqual(user.role, UserRole.COUNSELOR)
        self.assertEqual(user.status, UserStatus.PENDING)
        self.assertFalse(user.is_active)
        self.assertNotIn("_auth_user_id", client.session)

    def test_non_email_integrity_error_hides_database_detail(self):
        client = Client()
        with patch.object(
            SignUpForm,
            "save",
            side_effect=IntegrityError(
                "UNIQUE constraint failed: accounts_counselorprofile.user_id "
                "DETAIL secret@example.com"
            ),
        ):
            response = client.post(
                reverse("accounts:signup"),
                self._signup("generic-fail@example.com"),
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "회원가입을 완료하지 못했습니다")
        self.assertNotContains(response, "이미 가입된 이메일입니다.")
        self.assertNotContains(response, "accounts_counselorprofile")
        self.assertNotContains(response, "secret@example.com")
        self.assertFalse(User.objects.filter(email="generic-fail@example.com").exists())

    def test_signup_without_csrf_is_forbidden(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(
            reverse("accounts:signup"),
            self._signup("csrf@example.com"),
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email="csrf@example.com").exists())

    def test_cache_outage_does_not_block_signup(self):
        with patch("apps.counseling.apply_registration.cache.add", side_effect=ConnectionError):
            allowed = guest_request_is_limited(
                "signup",
                RequestFactory().post("/accounts/signup/", REMOTE_ADDR="10.0.0.8"),
                "outage@example.com",
            )
        self.assertFalse(allowed)

    def test_forwarded_for_is_not_used_as_client_ip(self):
        request = RequestFactory().post(
            "/accounts/signup/",
            REMOTE_ADDR="10.1.1.1",
            HTTP_X_FORWARDED_FOR="203.0.113.9",
        )
        self.assertEqual(client_ip(request), "10.1.1.1")

    def test_same_address_limit_is_case_insensitive(self):
        factory = RequestFactory()
        first = factory.post("/accounts/signup/", REMOTE_ADDR="10.2.2.2")
        second = factory.post("/accounts/signup/", REMOTE_ADDR="10.3.3.3")
        with override_settings(SIGNUP_ADDRESS_LIMIT=1, SIGNUP_IP_LIMIT=100, SIGNUP_WINDOW=600):
            self.assertFalse(guest_request_is_limited("signup", first, "Person@example.com"))
            self.assertTrue(guest_request_is_limited("signup", second, "person@example.com"))


class RateLimitCacheTests(TestCase):
    def test_expired_counter_is_not_overwritten(self):
        request = RequestFactory().post("/", REMOTE_ADDR="10.9.9.9")
        with patch(
            "apps.counseling.apply_registration.cache.incr",
            side_effect=ValueError("missing"),
        ):
            with patch(
                "apps.counseling.apply_registration.cache.add",
                return_value=True,
            ) as added:
                with patch("apps.counseling.apply_registration.cache.set") as overwritten:
                    limited = guest_request_is_limited("signup", request, "")
        self.assertFalse(limited)
        self.assertTrue(added.called)
        overwritten.assert_not_called()

    def test_locmem_counts_past_the_limit(self):
        cache.clear()
        request = RequestFactory().post("/", REMOTE_ADDR="192.0.2.10")
        with override_settings(SIGNUP_IP_LIMIT=2, SIGNUP_WINDOW=600):
            self.assertFalse(guest_request_is_limited("signup", request, ""))
            self.assertFalse(guest_request_is_limited("signup", request, ""))
            self.assertTrue(guest_request_is_limited("signup", request, ""))
