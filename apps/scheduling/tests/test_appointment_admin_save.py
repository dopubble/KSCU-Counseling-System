"""Django Admin 예약 확정 시 Zoom 연동(save_model) 테스트."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.scheduling.admin import AppointmentAdmin, _admin_intends_remote_confirm
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.services import AppointmentServiceError
from apps.sessions_app.models import ZoomMeeting


def _create_client(name: str = "내담자") -> User:
    return User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.CLIENT,
        status=UserStatus.ACTIVE,
    )


def _create_counselor(name: str = "상담사") -> User:
    user = User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.COUNSELOR,
        status=UserStatus.ACTIVE,
    )
    CounselorProfile.objects.get_or_create(user=user, defaults={"cohort": 1})
    return user


def _create_remote_case(client: User, counselor: User, label: str) -> Case:
    application = CounselingApplication.objects.create(
        client=client,
        counseling_types=["진로상담"],
        reason=label,
        counseling_method=CounselingMethod.REMOTE,
        status=ApplicationStatus.IN_PROGRESS,
    )
    return Case.objects.create(
        application=application,
        client=client,
        counselor=counselor,
        case_number=f"CASE-{label}",
        status=CaseStatus.ACTIVE,
        counseling_method=CounselingMethod.REMOTE,
    )


class _DummyForm:
    pass


@override_settings(ZOOM_LICENSED_USERS="host1@example.com,host2@example.com")
class AppointmentAdminSaveModelTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.appointment_admin = AppointmentAdmin(Appointment, self.site)
        self.factory = RequestFactory()
        self.counselor = _create_counselor()
        self.client_user = _create_client()
        self.case = _create_remote_case(self.client_user, self.counselor, "ADMIN")
        self.scheduled_at = timezone.now().replace(
            hour=14, minute=0, second=0, microsecond=0
        ) + timedelta(days=7)
        self.request = self.factory.post("/admin/scheduling/appointment/add/")
        setattr(self.request, "session", "session")
        messages_storage = FallbackStorage(self.request)
        setattr(self.request, "_messages", messages_storage)
        self.form = _DummyForm()

    def _edited_copy(self, appointment: Appointment, **fields) -> Appointment:
        appointment.refresh_from_db()
        for key, value in fields.items():
            setattr(appointment, key, value)
        return appointment

    def test_admin_intends_remote_confirm_for_new_confirmed_remote(self):
        obj = Appointment(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.CONFIRMED,
            session_number=1,
        )
        self.assertTrue(
            _admin_intends_remote_confirm(obj, change=False, form=self.form)
        )

    def test_admin_intends_remote_confirm_false_for_in_person(self):
        self.case.counseling_method = CounselingMethod.IN_PERSON
        self.case.save(update_fields=["counseling_method"])
        obj = Appointment(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            status=AppointmentStatus.CONFIRMED,
        )
        self.assertFalse(
            _admin_intends_remote_confirm(obj, change=False, form=self.form)
        )

    def test_admin_intends_remote_confirm_uses_fresh_case_from_db(self):
        """폼 인스턴스에 캐시된 대면 Case가 있어도 DB가 REMOTE면 Zoom 경로."""
        self.case.counseling_method = CounselingMethod.IN_PERSON
        self.case.save(update_fields=["counseling_method"])
        obj = Appointment(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            status=AppointmentStatus.CONFIRMED,
            session_number=4,
        )
        Case.objects.filter(pk=self.case.pk).update(
            counseling_method=CounselingMethod.REMOTE
        )
        self.assertTrue(
            _admin_intends_remote_confirm(obj, change=False, form=self.form)
        )

    def test_admin_intends_remote_confirm_for_pending_to_confirmed_edit(self):
        appointment = Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.PENDING,
            session_number=5,
        )
        form = _DummyForm()
        form.initial = {"status": AppointmentStatus.PENDING}
        obj = self._edited_copy(appointment, status=AppointmentStatus.CONFIRMED)
        self.assertTrue(
            _admin_intends_remote_confirm(obj, change=True, form=form)
        )

    def test_admin_intends_remote_confirm_for_confirmed_without_zoom(self):
        appointment = Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.CONFIRMED,
            confirmed_at=timezone.now(),
            session_number=6,
        )
        form = _DummyForm()
        form.initial = {"status": AppointmentStatus.CONFIRMED}
        obj = self._edited_copy(appointment, status=AppointmentStatus.CONFIRMED)
        self.assertTrue(
            _admin_intends_remote_confirm(obj, change=True, form=form)
        )

    def test_admin_intends_remote_confirm_skips_confirmed_with_zoom(self):
        appointment = Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.CONFIRMED,
            confirmed_at=timezone.now(),
            session_number=7,
        )
        ZoomMeeting.objects.create(
            appointment=appointment,
            zoom_meeting_id="12345",
            join_url="https://zoom.us/j/12345",
        )
        form = _DummyForm()
        form.initial = {"status": AppointmentStatus.CONFIRMED}
        obj = self._edited_copy(appointment, status=AppointmentStatus.CONFIRMED)
        self.assertFalse(
            _admin_intends_remote_confirm(obj, change=True, form=form)
        )

    @patch("apps.scheduling.services.create_zoom_meeting")
    @patch("apps.scheduling.services.get_zoom_meeting")
    @patch("apps.scheduling.services.update_zoom_meeting_participant_settings")
    def test_save_model_confirms_remote_and_creates_zoom(
        self,
        _mock_patch_settings,
        mock_get_zoom,
        mock_create_zoom_api,
    ):
        mock_create_zoom_api.return_value = {
            "id": "999",
            "join_url": "https://zoom.us/j/999",
            "start_url": "",
            "password": "",
        }
        mock_get_zoom.return_value = mock_create_zoom_api.return_value

        obj = Appointment(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.CONFIRMED,
            session_number=1,
        )
        self.appointment_admin.save_model(self.request, obj, self.form, change=False)

        obj.refresh_from_db()
        self.assertEqual(obj.status, AppointmentStatus.CONFIRMED)
        self.assertIsNotNone(obj.confirmed_at)
        self.assertTrue(ZoomMeeting.objects.filter(appointment=obj).exists())
        mock_create_zoom_api.assert_called_once()

    @patch(
        "apps.scheduling.admin.confirm_appointment_with_zoom",
        side_effect=AppointmentServiceError("호스트 없음"),
    )
    def test_save_model_zoom_failure_rolls_back(self, _mock_confirm):
        obj = Appointment(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.CONFIRMED,
            session_number=1,
        )
        self.appointment_admin.save_model(self.request, obj, self.form, change=False)

        self.assertFalse(Appointment.objects.filter(case=self.case).exists())
        self.assertTrue(self.appointment_admin._zoom_save_failed(self.request))
        stored = list(messages.get_messages(self.request))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].level, messages.ERROR)

    @patch("apps.scheduling.services.create_zoom_meeting")
    @patch("apps.scheduling.services.get_zoom_meeting")
    @patch("apps.scheduling.services.update_zoom_meeting_participant_settings")
    def test_pending_to_confirmed_via_admin_triggers_zoom(
        self,
        _mock_patch_settings,
        mock_get_zoom,
        mock_create_zoom_api,
    ):
        mock_create_zoom_api.return_value = {
            "id": "888",
            "join_url": "https://zoom.us/j/888",
            "start_url": "",
            "password": "",
        }
        mock_get_zoom.return_value = mock_create_zoom_api.return_value
        appointment = Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.PENDING,
            session_number=2,
        )

        obj = self._edited_copy(appointment, status=AppointmentStatus.CONFIRMED)
        self.appointment_admin.save_model(self.request, obj, self.form, change=True)

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, AppointmentStatus.CONFIRMED)
        self.assertTrue(ZoomMeeting.objects.filter(appointment=appointment).exists())

    @patch("apps.scheduling.services.create_zoom_meeting")
    @patch("apps.scheduling.services.get_zoom_meeting")
    @patch("apps.scheduling.services.update_zoom_meeting_participant_settings")
    def test_confirmed_without_zoom_reissue_on_resave(
        self,
        _mock_patch_settings,
        mock_get_zoom,
        mock_create_zoom_api,
    ):
        mock_create_zoom_api.return_value = {
            "id": "777",
            "join_url": "https://zoom.us/j/777",
            "start_url": "",
            "password": "",
        }
        mock_get_zoom.return_value = mock_create_zoom_api.return_value

        appointment = Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.CONFIRMED,
            confirmed_at=timezone.now(),
            session_number=8,
        )
        form = _DummyForm()
        form.initial = {"status": AppointmentStatus.CONFIRMED}
        obj = self._edited_copy(appointment, status=AppointmentStatus.CONFIRMED)
        self.appointment_admin.save_model(self.request, obj, form, change=True)

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, AppointmentStatus.CONFIRMED)
        self.assertTrue(ZoomMeeting.objects.filter(appointment=appointment).exists())
        mock_create_zoom_api.assert_called_once()

    @patch("apps.scheduling.admin.confirm_appointment_with_zoom")
    def test_already_confirmed_with_zoom_does_not_reconfirm(self, mock_confirm):
        appointment = Appointment.objects.create(
            case=self.case,
            counselor=self.counselor,
            client=self.client_user,
            scheduled_at=self.scheduled_at,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.CONFIRMED,
            confirmed_at=timezone.now(),
            session_number=3,
        )
        ZoomMeeting.objects.create(
            appointment=appointment,
            zoom_meeting_id="33333",
            join_url="https://zoom.us/j/33333",
        )
        form = _DummyForm()
        form.initial = {"status": AppointmentStatus.CONFIRMED}
        new_time = self.scheduled_at + timedelta(days=1)
        obj = self._edited_copy(appointment, scheduled_at=new_time)
        self.appointment_admin.save_model(self.request, obj, form, change=True)
        mock_confirm.assert_not_called()
        appointment.refresh_from_db()
        self.assertEqual(appointment.scheduled_at, new_time)
