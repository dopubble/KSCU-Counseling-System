"""상담 일정 달력(Flatpickr) 페이지 공통 컨텍스트."""

from __future__ import annotations

from django.urls import reverse

from apps.counseling.models import Case, CounselingMethod
from apps.scheduling.availability import (
    get_counselor_blocked_dates,
    serialize_counselor_availability_rules,
)
from apps.scheduling.constants import (
    BOOKING_SLOT_INTERVAL_MINUTES,
    DEFAULT_APPOINTMENT_DURATION_MINUTES,
)
from apps.scheduling.models import Appointment
from apps.scheduling.remote_zoom_capacity import remote_zoom_capacity_limit


def build_schedule_picker_context(
    case: Case,
    *,
    appointment: Appointment | None = None,
    counselor=None,
) -> dict:
    """내담자·상담사 예약 달력 초기화용."""
    counselor = counselor or case.counselor
    is_remote = case.counseling_method == CounselingMethod.REMOTE
    duration = (
        appointment.duration_minutes
        if appointment and appointment.duration_minutes
        else DEFAULT_APPOINTMENT_DURATION_MINUTES
    )
    exclude_id = str(appointment.pk) if appointment and appointment.pk else ""
    intervals_url = reverse("scheduling:remote_zoom_busy_intervals")
    return {
        "schedule_picker_enabled": bool(counselor),
        "schedule_picker_remote": is_remote,
        "schedule_picker_zoom_capacity": remote_zoom_capacity_limit(),
        "schedule_picker_duration_minutes": duration,
        "schedule_picker_exclude_appointment_id": exclude_id,
        "schedule_picker_zoom_intervals_url": intervals_url,
        "schedule_picker_config": {
            "remote": is_remote,
            "zoomCapacity": remote_zoom_capacity_limit(),
            "durationMinutes": duration,
            "excludeAppointmentId": exclude_id,
            "zoomIntervalsUrl": intervals_url,
            "slotIntervalMinutes": BOOKING_SLOT_INTERVAL_MINUTES,
        },
        "counselor_blocked_dates": (
            get_counselor_blocked_dates(counselor.pk) if counselor else []
        ),
        "counselor_availability_rules": (
            serialize_counselor_availability_rules(counselor) if counselor else []
        ),
    }
