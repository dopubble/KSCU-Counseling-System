"""Admin Zoom Host 목록 · 읽기 우선순위 · 연결 테스트."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CounselorProfile, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.scheduling.admin import RemoteZoomSchedulingSettingsForm
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus, RemoteZoomSchedulingSettings
from apps.scheduling.services import confirm_appointment_with_zoom, recreate_all_zoom_meetings
from apps.scheduling.utils import verify_zoom_host_emails
from apps.scheduling.zoom_account import zoom_meeting_account_kind
from apps.sessions_app.models import ZoomMeeting
from apps.scheduling.zoom_hosts import (
    get_zoom_licensed_user_emails,
    parse_licensed_user_email_lines,
)


def _set_db_hosts(*emails: str) -> RemoteZoomSchedulingSettings:
    row, _created = RemoteZoomSchedulingSettings.objects.get_or_create(
        pk=RemoteZoomSchedulingSettings.SETTINGS_PK,
        defaults={"simultaneous_session_capacity": 2},
    )
    row.licensed_user_emails = "\n".join(emails)
    if emails:
        row.simultaneous_session_capacity = min(2, len(emails)) or 1
    row.save()
    return row


class ParseLicensedUserEmailsTests(TestCase):
    def test_preserves_order_and_strips_blank_lines(self):
        emails, errors = parse_licensed_user_email_lines(
            "  a@example.com \n\n b@example.com \n c@example.com "
        )
        self.assertEqual(errors, [])
        self.assertEqual(emails, ("a@example.com", "b@example.com", "c@example.com"))

    def test_rejects_invalid_email(self):
        emails, errors = parse_licensed_user_email_lines("not-an-email")
        self.assertEqual(emails, ())
        self.assertTrue(errors)

    def test_rejects_duplicates(self):
        emails, errors = parse_licensed_user_email_lines(
            "a@example.com\nA@example.com"
        )
        self.assertEqual(emails, ("a@example.com",))
        self.assertTrue(any("중복" in item for item in errors))


@override_settings(ZOOM_LICENSED_USERS="env1@example.com,env2@example.com")
class LicensedUserEmailPriorityTests(TestCase):
    def test_db_hosts_win_over_env(self):
        _set_db_hosts("db1@example.com", "db2@example.com")
        self.assertEqual(
            get_zoom_licensed_user_emails(),
            ("db1@example.com", "db2@example.com"),
        )

    def test_empty_db_falls_back_to_env(self):
        _set_db_hosts()
        row = RemoteZoomSchedulingSettings.objects.get(
            pk=RemoteZoomSchedulingSettings.SETTINGS_PK
        )
        row.licensed_user_emails = ""
        row.save(update_fields=["licensed_user_emails"])
        self.assertEqual(
            get_zoom_licensed_user_emails(),
            ("env1@example.com", "env2@example.com"),
        )

    @override_settings(ZOOM_LICENSED_USERS="")
    def test_empty_db_and_env_uses_code_default(self):
        row = RemoteZoomSchedulingSettings.objects.filter(
            pk=RemoteZoomSchedulingSettings.SETTINGS_PK
        ).first()
        if row:
            row.licensed_user_emails = ""
            row.save(update_fields=["licensed_user_emails"])
        from apps.scheduling.zoom_hosts import DEFAULT_ZOOM_LICENSED_USERS

        self.assertEqual(get_zoom_licensed_user_emails(), DEFAULT_ZOOM_LICENSED_USERS)

    def test_three_or_more_hosts(self):
        _set_db_hosts("a@example.com", "b@example.com", "c@example.com")
        self.assertEqual(
            get_zoom_licensed_user_emails(),
            ("a@example.com", "b@example.com", "c@example.com"),
        )


class VerifyZoomHostEmailsTests(TestCase):
    @patch("apps.scheduling.utils.list_zoom_users")
    @patch("apps.scheduling.utils.get_zoom_access_token", return_value="token")
    def test_success_when_users_are_licensed(self, _token, mock_users):
        mock_users.return_value = [
            {"email": "a@example.com", "type": 2},
            {"email": "b@example.com", "type": 2},
        ]
        ok, messages = verify_zoom_host_emails(["a@example.com", "b@example.com"])
        self.assertTrue(ok)
        self.assertTrue(any("성공" in item for item in messages))

    @patch("apps.scheduling.utils.list_zoom_users")
    @patch("apps.scheduling.utils.get_zoom_access_token", return_value="token")
    def test_missing_user(self, _token, mock_users):
        mock_users.return_value = [{"email": "a@example.com", "type": 2}]
        ok, messages = verify_zoom_host_emails(["a@example.com", "missing@example.com"])
        self.assertFalse(ok)
        self.assertTrue(any("찾을 수 없습니다" in item for item in messages))

    @patch("apps.scheduling.utils.list_zoom_users")
    @patch("apps.scheduling.utils.get_zoom_access_token", return_value="token")
    def test_basic_user_is_not_licensed(self, _token, mock_users):
        mock_users.return_value = [{"email": "a@example.com", "type": 1}]
        ok, messages = verify_zoom_host_emails(["a@example.com"])
        self.assertFalse(ok)
        self.assertTrue(any("Licensed" in item for item in messages))

    @patch(
        "apps.scheduling.utils.get_zoom_access_token",
        side_effect=Exception("should not use ZoomAPIError subclass wait"),
    )
    def test_auth_failure_message(self, mock_token):
        from apps.scheduling.utils import ZoomAPIError

        mock_token.side_effect = ZoomAPIError("bad")
        ok, messages = verify_zoom_host_emails(["a@example.com"])
        self.assertFalse(ok)
        self.assertEqual(messages, ["Zoom API 인증에 실패했습니다."])


class RemoteZoomSchedulingSettingsFormTests(TestCase):
    @patch("apps.scheduling.admin.verify_zoom_host_emails", return_value=(True, ["ok"]))
    def test_form_saves_verified_emails(self, _mock_verify):
        form = RemoteZoomSchedulingSettingsForm(
            data={
                "licensed_user_emails": "host-a@example.com\nhost-b@example.com",
                "simultaneous_session_capacity": 2,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["licensed_user_emails"],
            "host-a@example.com\nhost-b@example.com",
        )

    def test_form_rejects_bad_email_without_api(self):
        form = RemoteZoomSchedulingSettingsForm(
            data={
                "licensed_user_emails": "not-valid",
                "simultaneous_session_capacity": 2,
            }
        )
        self.assertFalse(form.is_valid())

    @patch(
        "apps.scheduling.admin.verify_zoom_host_emails",
        return_value=(False, ["x@example.com 사용자를 찾을 수 없습니다."]),
    )
    def test_form_rejects_failed_connection_test(self, _mock_verify):
        form = RemoteZoomSchedulingSettingsForm(
            data={
                "licensed_user_emails": "x@example.com",
                "simultaneous_session_capacity": 1,
            }
        )
        self.assertFalse(form.is_valid())


@override_settings(ZOOM_LICENSED_USERS="env1@example.com,env2@example.com")
class ZoomHostAdminPermissionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            email="root@example.com",
            password="pass12345",
            name="Root",
        )
        self.staff = User.objects.create_user(
            email="staff@example.com",
            password="pass12345",
            name="Staff",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
            is_staff=True,
            is_superuser=False,
        )
        self.change_url = reverse(
            "admin:scheduling_remotezoomschedulingsettings_change",
            args=[RemoteZoomSchedulingSettings.SETTINGS_PK],
        )
        RemoteZoomSchedulingSettings.objects.get_or_create(
            pk=RemoteZoomSchedulingSettings.SETTINGS_PK,
            defaults={"simultaneous_session_capacity": 2},
        )

    def test_staff_cannot_post_change(self):
        client = Client()
        client.force_login(self.staff)
        response = client.post(
            self.change_url,
            {
                "licensed_user_emails": "a@example.com\nb@example.com",
                "simultaneous_session_capacity": 2,
            },
        )
        self.assertEqual(response.status_code, 403)

    @patch("apps.scheduling.admin.verify_zoom_host_emails", return_value=(True, ["ok"]))
    def test_superuser_can_save(self, _mock_verify):
        client = Client()
        client.force_login(self.superuser)
        response = client.post(
            self.change_url,
            {
                "licensed_user_emails": "a@example.com\nb@example.com",
                "simultaneous_session_capacity": 2,
            },
        )
        self.assertEqual(response.status_code, 302)
        row = RemoteZoomSchedulingSettings.objects.get(
            pk=RemoteZoomSchedulingSettings.SETTINGS_PK
        )
        self.assertIn("a@example.com", row.licensed_user_emails)
        self.assertIsNotNone(row.last_verified_at)
        self.assertEqual(row.updated_by_id, self.superuser.pk)


@override_settings(
    ZOOM_ACCOUNT_ID="acct-current",
    ZOOM_CLIENT_ID="cid",
    ZOOM_CLIENT_SECRET="sec",
    ZOOM_LICENSED_USERS="env-old@example.com,env-old2@example.com",
)
class NewMeetingUsesDbHostPoolTests(TestCase):
    def test_confirm_uses_db_host_and_stamps_account_id(self):
        _set_db_hosts("db-host1@example.com", "db-host2@example.com")
        User = get_user_model()
        client_user = User.objects.create_user(
            email="newmeet-client@example.com",
            password="pass12345",
            name="신규내담",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        counselor = User.objects.create_user(
            email="newmeet-counselor@example.com",
            password="pass12345",
            name="신규상담",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.get_or_create(user=counselor, defaults={"cohort": 1})
        application = CounselingApplication.objects.create(
            client=client_user,
            counseling_types=["진로상담"],
            reason="new",
            counseling_method=CounselingMethod.REMOTE,
            status=ApplicationStatus.IN_PROGRESS,
        )
        case = Case.objects.create(
            application=application,
            client=client_user,
            counselor=counselor,
            case_number="CASE-NEWHOST",
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.REMOTE,
        )
        pending = Appointment.objects.create(
            case=case,
            counselor=counselor,
            client=client_user,
            scheduled_at=timezone.now() + timedelta(days=5),
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.PENDING,
        )
        with (
            patch(
                "apps.scheduling.services.create_zoom_meeting",
                return_value={
                    "id": "999000111",
                    "join_url": "https://zoom.us/j/999000111",
                    "start_url": "https://zoom.us/s/999000111",
                },
            ) as create_mock,
            patch(
                "apps.scheduling.services.update_zoom_meeting_participant_settings"
            ),
            patch(
                "apps.scheduling.services.get_zoom_meeting",
                return_value={
                    "id": "999000111",
                    "join_url": "https://zoom.us/j/999000111",
                    "start_url": "https://zoom.us/s/999000111",
                },
            ),
            patch(
                "apps.scheduling.duplicate_zoom_host_fix.is_zoom_configured",
                return_value=True,
            ),
        ):
            appointment, zoom = confirm_appointment_with_zoom(pending, notify=False)

        self.assertEqual(
            create_mock.call_args.kwargs["host_user_email"],
            "db-host1@example.com",
        )
        self.assertEqual(zoom.zoom_host_email, "db-host1@example.com")
        self.assertEqual(zoom.zoom_account_id, "acct-current")
        self.assertEqual(zoom.zoom_meeting_id, "999000111")


@override_settings(
    ZOOM_ACCOUNT_ID="acct-current",
    ZOOM_CLIENT_ID="cid",
    ZOOM_CLIENT_SECRET="sec",
    ZOOM_LICENSED_USERS="db-host1@example.com,db-host2@example.com",
)
class RecreateZoomAccountIdTests(TestCase):
    def _confirmed_remote_with_zoom(self, *, zoom_account_id: str) -> Appointment:
        User = get_user_model()
        client_user = User.objects.create_user(
            email="recreate-client@example.com",
            password="pass12345",
            name="재생성내담",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        counselor = User.objects.create_user(
            email="recreate-counselor@example.com",
            password="pass12345",
            name="재생성상담",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.get_or_create(user=counselor, defaults={"cohort": 1})
        application = CounselingApplication.objects.create(
            client=client_user,
            counseling_types=["진로상담"],
            reason="recreate",
            counseling_method=CounselingMethod.REMOTE,
            status=ApplicationStatus.IN_PROGRESS,
        )
        case = Case.objects.create(
            application=application,
            client=client_user,
            counselor=counselor,
            case_number="CASE-RECREATE",
            status=CaseStatus.ACTIVE,
            counseling_method=CounselingMethod.REMOTE,
        )
        appointment = Appointment.objects.create(
            case=case,
            counselor=counselor,
            client=client_user,
            scheduled_at=timezone.now() + timedelta(days=6),
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.CONFIRMED,
            confirmed_at=timezone.now(),
        )
        ZoomMeeting.objects.create(
            appointment=appointment,
            zoom_meeting_id="old-meeting-id",
            join_url="https://zoom.us/j/old-meeting-id",
            start_url="https://zoom.us/s/old-meeting-id",
            zoom_host_email="db-host1@example.com",
            zoom_account_id=zoom_account_id,
        )
        return appointment

    def test_recreate_stamps_current_zoom_account_id(self):
        appointment = self._confirmed_remote_with_zoom(zoom_account_id="old-account-id")
        zoom = appointment.zoom_meeting
        self.assertEqual(zoom.zoom_account_id, "old-account-id")
        self.assertEqual(zoom_meeting_account_kind(zoom), "legacy")

        with (
            patch(
                "apps.scheduling.services.appointment_zoom_link_is_locked",
                return_value=False,
            ),
            patch(
                "apps.scheduling.services.create_zoom_meeting",
                return_value={
                    "id": "recreated-meeting-id",
                    "join_url": "https://zoom.us/j/recreated-meeting-id",
                    "start_url": "https://zoom.us/s/recreated-meeting-id",
                },
            ),
            patch("apps.scheduling.services.update_zoom_meeting_participant_settings"),
            patch(
                "apps.scheduling.services.get_zoom_meeting",
                return_value={
                    "id": "recreated-meeting-id",
                    "join_url": "https://zoom.us/j/recreated-meeting-id",
                    "start_url": "https://zoom.us/s/recreated-meeting-id",
                },
            ),
            patch("apps.scheduling.services.delete_zoom_meeting"),
        ):
            recreated, skipped, errors = recreate_all_zoom_meetings(dry_run=False)

        self.assertEqual(recreated, 1)
        self.assertEqual(skipped, 0)
        self.assertEqual(errors, [])
        zoom.refresh_from_db()
        self.assertEqual(zoom.zoom_meeting_id, "recreated-meeting-id")
        self.assertEqual(zoom.join_url, "https://zoom.us/j/recreated-meeting-id")
        self.assertEqual(zoom.zoom_account_id, "acct-current")
        self.assertEqual(zoom_meeting_account_kind(zoom), "current")

    def test_recreate_skips_locked_and_keeps_zoom_account_id(self):
        appointment = self._confirmed_remote_with_zoom(zoom_account_id="old-account-id")
        with patch("apps.scheduling.services.create_zoom_meeting") as create_mock:
            recreated, skipped, errors = recreate_all_zoom_meetings(dry_run=False)

        self.assertEqual(recreated, 0)
        self.assertEqual(skipped, 1)
        self.assertEqual(errors, [])
        create_mock.assert_not_called()
        zoom = ZoomMeeting.objects.get(appointment=appointment)
        self.assertEqual(zoom.zoom_meeting_id, "old-meeting-id")
        self.assertEqual(zoom.join_url, "https://zoom.us/j/old-meeting-id")
        self.assertEqual(zoom.zoom_account_id, "old-account-id")
        self.assertEqual(zoom_meeting_account_kind(zoom), "legacy")
