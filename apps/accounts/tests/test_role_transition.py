from django.contrib.admin.sites import site
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import (
    CounselorProfile,
    SupervisorProfile,
    User,
    UserRole,
    UserStatus,
)


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
