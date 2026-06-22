"""예약 캘린더 — 날짜별 시간 슬롯 상태 계산."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from django.utils import timezone

from apps.counseling.models import Case, CounselingMethod
from apps.reports.appointment_calendar import _calendar_localtime, _intervals_overlap
from apps.scheduling.availability import (
    get_counselor_blocked_dates,
    is_counselor_slot_available,
    local_timezone,
)
from apps.scheduling.constants import (
    BOOKING_SLOT_END_HOUR,
    BOOKING_SLOT_START_HOUR,
    DEFAULT_APPOINTMENT_DURATION_MINUTES,
)
from apps.scheduling.in_person_room_capacity import (
    count_overlapping_confirmed_in_person,
    in_person_room_capacity_limit,
)
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.remote_zoom_capacity import (
    appointment_duration_minutes,
    count_overlapping_confirmed_remote,
    remote_zoom_capacity_limit,
)

SlotState = Literal[
    "available",
    "blocked",
    "taken",
    "zoom_full",
    "room_full",
]


@dataclass(frozen=True)
class BookingSlot:
    start: datetime
    end: datetime
    state: SlotState
    label: str

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "state": self.state,
            "label": self.label,
        }


def _slot_label(start: datetime, end: datetime) -> str:
    return f"{start:%H:%M} – {end:%H:%M}"


def hourly_slot_starts(on_date: date) -> list[datetime]:
    """09:00~21:00 시작 (60분 상담 시 22:00 종료)."""
    tz = local_timezone()
    slots: list[datetime] = []
    for hour in range(BOOKING_SLOT_START_HOUR, BOOKING_SLOT_END_HOUR):
        if hour + (DEFAULT_APPOINTMENT_DURATION_MINUTES // 60) > BOOKING_SLOT_END_HOUR:
            break
        slots.append(
            timezone.make_aware(datetime.combine(on_date, time(hour, 0)), tz)
        )
    return slots


def counselor_has_overlapping_appointment(
    counselor_id,
    scheduled_at: datetime,
    duration_minutes: int,
    *,
    exclude_appointment_id=None,
) -> bool:
    start = _calendar_localtime(scheduled_at)
    end = start + timedelta(minutes=duration_minutes)

    peers = Appointment.objects.filter(
        counselor_id=counselor_id,
        status__in=[AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED],
    )
    if exclude_appointment_id:
        peers = peers.exclude(pk=exclude_appointment_id)

    for appointment in peers.iterator():
        peer_start = _calendar_localtime(appointment.scheduled_at)
        peer_end = peer_start + timedelta(
            minutes=appointment_duration_minutes(appointment)
        )
        if _intervals_overlap(start, end, peer_start, peer_end):
            return True
    return False


def _venue_capacity_state(
    *,
    counseling_method: str,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id=None,
) -> SlotState | None:
    if counseling_method == CounselingMethod.REMOTE:
        limit = remote_zoom_capacity_limit()
        if limit <= 0:
            return None
        count = count_overlapping_confirmed_remote(
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
            exclude_appointment_id=exclude_appointment_id,
        )
        if count >= limit:
            return "zoom_full"
    elif counseling_method == CounselingMethod.IN_PERSON:
        limit = in_person_room_capacity_limit()
        if limit <= 0:
            return None
        count = count_overlapping_confirmed_in_person(
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
            exclude_appointment_id=exclude_appointment_id,
        )
        if count >= limit:
            return "room_full"
    return None


def resolve_slot_state(
    *,
    counselor_id,
    counseling_method: str,
    scheduled_at: datetime,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    exclude_appointment_id=None,
    require_full_duration: bool = False,
) -> SlotState:
    available, _message = is_counselor_slot_available(
        counselor_id,
        scheduled_at,
        duration_minutes=duration_minutes,
        require_full_duration=require_full_duration,
    )
    if not available:
        return "blocked"

    if counselor_has_overlapping_appointment(
        counselor_id,
        scheduled_at,
        duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    ):
        return "taken"

    venue_state = _venue_capacity_state(
        counseling_method=counseling_method,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    )
    if venue_state:
        return venue_state

    return "available"


def build_booking_slots_for_date(
    *,
    case: Case,
    on_date: date,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    exclude_appointment_id=None,
    require_full_duration: bool = False,
) -> list[BookingSlot]:
    """사례 기준 — 하루 시간 슬롯 목록."""
    if not case.counselor_id:
        return []

    counseling_method = case.counseling_method
    slots: list[BookingSlot] = []

    for slot_start in hourly_slot_starts(on_date):
        slot_end = slot_start + timedelta(minutes=duration_minutes)
        state = resolve_slot_state(
            counselor_id=case.counselor_id,
            counseling_method=counseling_method,
            scheduled_at=slot_start,
            duration_minutes=duration_minutes,
            exclude_appointment_id=exclude_appointment_id,
            require_full_duration=require_full_duration,
        )
        slots.append(
            BookingSlot(
                start=slot_start,
                end=slot_end,
                state=state,
                label=_slot_label(slot_start, slot_end),
            )
        )
    return slots


def date_has_bookable_slot(
    *,
    case: Case,
    on_date: date,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    exclude_appointment_id=None,
    require_full_duration: bool = False,
) -> bool:
    """월간 달력 — 예약 가능한 슬롯이 하나라도 있는 날."""
    blocked_dates = get_counselor_blocked_dates(case.counselor_id)
    if on_date.isoformat() in blocked_dates:
        return False

    for slot in build_booking_slots_for_date(
        case=case,
        on_date=on_date,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
        require_full_duration=require_full_duration,
    ):
        if slot.state == "available":
            return True
    return False
