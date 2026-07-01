from django.contrib.admin.sites import site
from django.test import Client, TestCase
from django.urls import reverse
import re

from apps.accounts.models import (
    CounselorProfile,
    SupervisorProfile,
    User,
    UserRole,
    UserStatus,
)


def _build_admin_post_from_get(client, change_url, *, role_override=None):
    """GET 응답 HTML에서 Admin이 기대하는 POST 필드를 그대로 구성."""
    response = client.get(change_url)
    html = response.content.decode()
    csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    post = {"csrfmiddlewaretoken": csrf.group(1) if csrf else ""}

    for match in re.finditer(r"<input[^>]+name=\"([^\"]+)\"[^>]*>", html):
        name = match.group(1)
        tag = match.group(0)
        if name in post:
            continue
        if 'type="hidden"' in tag:
            vm = re.search(r'value="([^"]*)"', tag)
            post[name] = vm.group(1) if vm else ""
        elif name in ("is_staff", "is_superuser"):
            post[name] = ""
        elif 'type="checkbox"' in tag:
            post[name] = "on" if "checked" in tag else ""
        else:
            vm = re.search(r'value="([^"]*)"', tag)
            if vm:
                post[name] = vm.group(1)

    for match in re.finditer(
        r'<select[^>]+name="([^"]+)"[^>]*>(.*?)</select>', html, re.S
    ):
        name = match.group(1)
        body = match.group(2)
        selected = re.search(r'<option value="([^"]*)" selected', body)
        post[name] = selected.group(1) if selected else ""

    if role_override is not None:
        post["role"] = role_override
    post["_save"] = "저장"
    return post, html


class RoleTransitionTests(TestCase):
    def test_counselor_to_supervisor_migrates_cohort_via_save(self):
        user = User.objects.create_user(
            email="counselor-to-super@example.com",
            password="pass",
            name="전환상담사",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        profile = user.counselor_profile
        profile.cohort = 2
        profile.is_approved = True
        profile.save(update_fields=["cohort", "is_approved", "updated_at"])

        user.role = UserRole.SUPERVISOR
        user.save()

        self.assertFalse(CounselorProfile.objects.filter(user=user).exists())
        self.assertTrue(SupervisorProfile.objects.filter(user=user).exists())
        self.assertEqual(user.supervisor_profile.assigned_cohorts, [2])

    def test_admin_can_change_counselor_to_supervisor(self):
        admin = User.objects.create_superuser(
            email="admin-role@example.com",
            password="Adminpass123!",
            name="관리자",
        )
        counselor = User.objects.create_user(
            email="heo-super@example.com",
            password="pass",
            name="허미경",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        profile = counselor.counselor_profile
        profile.cohort = 1
        profile.is_approved = True
        profile.save(update_fields=["cohort", "is_approved", "updated_at"])

        client = Client()
        client.force_login(admin)
        change_url = reverse("admin:accounts_user_change", args=[counselor.pk])
        get_response = client.get(change_url)
        self.assertEqual(get_response.status_code, 200)

        post_data = {
            "email": counselor.email,
            "name": counselor.name,
            "phone": counselor.phone,
            "role": UserRole.SUPERVISOR,
            "status": UserStatus.ACTIVE,
            "is_staff": "",
            "is_superuser": "",
            "groups": [],
            "user_permissions": [],
            "supervisor_profile-TOTAL_FORMS": "0",
            "supervisor_profile-INITIAL_FORMS": "0",
            "supervisor_profile-MIN_NUM_FORMS": "0",
            "supervisor_profile-MAX_NUM_FORMS": "1",
            "_save": "Save",
        }
        response = client.post(change_url, post_data, follow=True)
        self.assertEqual(response.status_code, 200, msg=response.content.decode()[:500])

        counselor.refresh_from_db()
        self.assertEqual(counselor.role, UserRole.SUPERVISOR)
        self.assertFalse(CounselorProfile.objects.filter(user=counselor).exists())
        self.assertEqual(counselor.supervisor_profile.assigned_cohorts, [1])

    def test_admin_role_change_with_counselor_inline_left_in_post(self):
        """역할만 바꾸고 저장할 때(구 상담사 인라인 POST 잔존)에도 성공해야 함."""
        admin = User.objects.create_superuser(
            email="admin-inline@example.com",
            password="Adminpass123!",
            name="관리자",
        )
        counselor = User.objects.create_user(
            email="inline-heo@example.com",
            password="pass",
            name="허미경",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        profile = counselor.counselor_profile
        profile.cohort = 1
        profile.is_approved = True
        profile.save(update_fields=["cohort", "is_approved", "updated_at"])

        client = Client()
        client.force_login(admin)
        change_url = reverse("admin:accounts_user_change", args=[counselor.pk])
        post_data, _html = _build_admin_post_from_get(
            client, change_url, role_override=UserRole.SUPERVISOR
        )
        self.assertTrue(
            any(k.startswith("counselor_profile-") for k in post_data),
            "상담사 인라인 필드가 POST에 포함되어야 브라우저 시나리오와 동일",
        )

        response = client.post(change_url, post_data, follow=True)
        self.assertEqual(
            response.status_code,
            200,
            msg=response.content.decode()[:800],
        )
        self.assertNotIn("errorlist", response.content.decode())

        counselor.refresh_from_db()
        self.assertEqual(counselor.role, UserRole.SUPERVISOR)
        self.assertFalse(CounselorProfile.objects.filter(user=counselor).exists())
        self.assertEqual(counselor.supervisor_profile.assigned_cohorts, [1])
