"""legacy_sscukscu_host_migration — allowlist preflight 테스트."""

from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from django.test import SimpleTestCase
from django.utils import timezone
from zoneinfo import ZoneInfo

from apps.counseling.models import CounselingMethod
from apps.scheduling.legacy_sscukscu_host_migration import (
    ALLOWED_APPOINTMENT_IDS,
    LEGACY_HOST_EMAIL,
    SCHEDULED_FROM_KST,
    _validate_appointment,
    build_migration_plan,
    target_host_email,
)
from apps.scheduling.models import AppointmentStatus

KST = ZoneInfo("Asia/Seoul")
H2 = "sedulife@mail.kcu.ac"
ALLOWED_ONE = UUID("2f0d0208-89aa-488e-8e45-bfbc4d9af490")


def _make_apt(
    *,
    pk=None,
    scheduled_at=None,
    host_email=LEGACY_HOST_EMAIL,
    status=AppointmentStatus.CONFIRMED,
    method=CounselingMethod.REMOTE,
    meeting_id="123456789",
    join_url="https://zoom.us/j/123456789",
):
    pk = pk or ALLOWED_ONE
    scheduled_at = scheduled_at or timezone.make_aware(
        datetime(2026, 8, 19, 10, 0), KST
    )
    zoom = MagicMock()
    zoom.zoom_host_email = host_email
    zoom.zoom_meeting_id = meeting_id
    zoom.join_url = join_url

    case = MagicMock()
    case.counseling_method = method

    apt = MagicMock()
    apt.pk = pk
    apt.status = status
    apt.case = case
    apt.scheduled_at = scheduled_at
    apt.client.name = "테스트내담자"
    apt.counselor.name = "테스트상담사"
    apt.zoom_meeting = zoom
    return apt


class LegacySscukscuHostMigrationTests(SimpleTestCase):
    def test_allowlist_has_seventeen_uuids(self):
        self.assertEqual(len(ALLOWED_APPOINTMENT_IDS), 17)

    @patch(
        "apps.scheduling.legacy_sscukscu_host_migration.email_for_host_id",
        return_value=H2,
    )
    def test_target_host_email(self, *_mocks):
        self.assertEqual(target_host_email(), H2)

    def test_validate_valid_legacy_appointment(self):
        apt = _make_apt()
        item = _validate_appointment(apt, ALLOWED_ONE)
        self.assertTrue(item.valid)
        self.assertEqual(item.current_host, LEGACY_HOST_EMAIL)

    def test_validate_rejects_before_cutoff(self):
        apt = _make_apt(
            scheduled_at=timezone.make_aware(datetime(2026, 8, 18, 23, 59), KST)
        )
        item = _validate_appointment(apt, ALLOWED_ONE)
        self.assertFalse(item.valid)
        self.assertIn("scheduled_at", item.invalid_reason)

    def test_validate_rejects_non_legacy_host(self):
        apt = _make_apt(host_email="kcuplan@mail.kcu.ac")
        item = _validate_appointment(apt, ALLOWED_ONE)
        self.assertFalse(item.valid)
        self.assertIn("zoom_host_email", item.invalid_reason)

    def test_validate_rejects_missing_appointment(self):
        item = _validate_appointment(None, uuid4())
        self.assertFalse(item.valid)
        self.assertIn("not found", item.invalid_reason)

    def test_validate_rejects_not_confirmed(self):
        apt = _make_apt(status=AppointmentStatus.CANCELLED)
        item = _validate_appointment(apt, ALLOWED_ONE)
        self.assertFalse(item.valid)

    def test_validate_rejects_in_person(self):
        apt = _make_apt(method=CounselingMethod.IN_PERSON)
        item = _validate_appointment(apt, ALLOWED_ONE)
        self.assertFalse(item.valid)

    @patch("apps.scheduling.legacy_sscukscu_host_migration.Appointment")
    def test_build_migration_plan_iterates_all_allowlist(self, appointment_model):
        appointment_model.objects.filter.return_value.select_related.return_value.order_by.return_value = []
        plan = build_migration_plan()
        self.assertEqual(len(plan), 17)
        self.assertEqual(
            {item.appointment_id for item in plan},
            ALLOWED_APPOINTMENT_IDS,
        )

    def test_scheduled_from_cutoff_is_aug_19_kst(self):
        self.assertEqual(
            SCHEDULED_FROM_KST.astimezone(KST).replace(tzinfo=KST),
            datetime(2026, 8, 19, 0, 0, 0, tzinfo=KST),
        )
