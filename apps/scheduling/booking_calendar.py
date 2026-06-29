"""예약 캘린더 페이지 공통 컨텍스트."""

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
    IN_PERSON_ROOM_CAPACITY,
)
from apps.scheduling.in_person_room_capacity import in_person_room_capacity_limit
from apps.scheduling.models import Appointment
from apps.scheduling.remote_zoom_capacity import remote_zoom_capacity_limit


def build_booking_calendar_context(
    case: Case,
    *,
    appointment: Appointment | None = None,
    session_number: int | None = None,
    role: str = "client",
) -> dict:
    """FullCalendar 예약 페이지 초기화용."""
    counselor = case.counselor
    is_remote = case.counseling_method == CounselingMethod.REMOTE
    duration = DEFAULT_APPOINTMENT_DURATION_MINUTES
    exclude_id = str(appointment.pk) if appointment and appointment.pk else ""

    slots_url = reverse("scheduling:booking_slots")
    events_url = ""
    if role == "counselor":
        events_url = reverse("scheduling:counselor_calendar_events")

    return {
        "booking_calendar_enabled": bool(counselor),
        "booking_calendar_role": role,
        "booking_calendar_remote": is_remote,
        "booking_calendar_session_number": session_number,
        "booking_calendar_duration_minutes": duration,
        "booking_calendar_exclude_appointment_id": exclude_id,
        "booking_calendar_case_id": str(case.pk),
        "booking_calendar_slots_url": slots_url,
        "booking_calendar_events_url": events_url,
        "booking_calendar_zoom_capacity": remote_zoom_capacity_limit(),
        "booking_calendar_room_capacity": in_person_room_capacity_limit(),
        "booking_calendar_config": {
            "role": role,
            "remote": is_remote,
            "caseId": str(case.pk),
            "sessionNumber": session_number,
            "durationMinutes": duration,
            "excludeAppointmentId": exclude_id,
            "slotsUrl": slots_url,
            "eventsUrl": events_url,
            "zoomCapacity": remote_zoom_capacity_limit(),
            "roomCapacity": in_person_room_capacity_limit(),
            "inPersonRoomCapacity": IN_PERSON_ROOM_CAPACITY,
            "slotIntervalMinutes": BOOKING_SLOT_INTERVAL_MINUTES,
        },
        "counselor_blocked_dates": (
            get_counselor_blocked_dates(counselor.pk) if counselor else []
        ),
        "counselor_availability_rules": (
            serialize_counselor_availability_rules(counselor) if counselor else []
        ),
    }
