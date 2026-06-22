"""대면 상담실 동시 예약 용량 — 상담실 수만큼 겹치는 확정 예약 제한."""

from __future__ import annotations

from datetime import datetime, timedelta

from apps.counseling.models import CounselingMethod
from apps.reports.appointment_calendar import _calendar_localtime, _intervals_overlap
from apps.scheduling.constants import (
    DEFAULT_APPOINTMENT_DURATION_MINUTES,
    IN_PERSON_ROOM_CAPACITY,
)
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.remote_zoom_capacity import appointment_duration_minutes

IN_PERSON_ROOM_CAPACITY_FULL_MESSAGE = (
    "해당 시간대는 대면 상담실 예약이 마감되었습니다. 다른 시간을 선택해 주세요."
)


def in_person_room_capacity_limit() -> int:
    return IN_PERSON_ROOM_CAPACITY


def count_overlapping_confirmed_in_person(
    *,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id=None,
) -> int:
    """겹치는 확정 대면 예약 수."""
    start = _calendar_localtime(scheduled_at)
    end = start + timedelta(minutes=duration_minutes)

    peers = Appointment.objects.filter(
        status=AppointmentStatus.CONFIRMED,
        case__counseling_method=CounselingMethod.IN_PERSON,
    )
    if exclude_appointment_id:
        peers = peers.exclude(pk=exclude_appointment_id)

    count = 0
    for appointment in peers.iterator():
        peer_start = _calendar_localtime(appointment.scheduled_at)
        peer_end = peer_start + timedelta(
            minutes=appointment_duration_minutes(appointment)
        )
        if _intervals_overlap(start, end, peer_start, peer_end):
            count += 1
    return count


def check_in_person_room_capacity(
    appointment: Appointment,
    *,
    scheduled_at: datetime | None = None,
    duration_minutes: int | None = None,
    exclude_appointment_id=None,
) -> tuple[bool, str]:
    """대면 예약이 상담실 동시 용량 내인지 확인."""
    if appointment.case.counseling_method != CounselingMethod.IN_PERSON:
        return True, ""

    limit = in_person_room_capacity_limit()
    if limit <= 0:
        return True, ""

    when = scheduled_at or appointment.scheduled_at
    duration = (
        duration_minutes
        if duration_minutes is not None
        else appointment_duration_minutes(appointment)
    )
    exclude = (
        exclude_appointment_id
        if exclude_appointment_id is not None
        else appointment.pk
    )
    overlap_count = count_overlapping_confirmed_in_person(
        scheduled_at=when,
        duration_minutes=duration,
        exclude_appointment_id=exclude,
    )
    if overlap_count >= limit:
        return False, IN_PERSON_ROOM_CAPACITY_FULL_MESSAGE
    return True, ""


def ensure_in_person_room_capacity(
    appointment: Appointment,
    *,
    scheduled_at: datetime | None = None,
    duration_minutes: int | None = None,
    exclude_appointment_id=None,
) -> None:
    ok, message = check_in_person_room_capacity(
        appointment,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    )
    if not ok:
        from apps.scheduling.services import AppointmentServiceError

        raise AppointmentServiceError(message)


def get_in_person_busy_intervals(
    range_start: datetime,
    range_end: datetime,
    *,
    exclude_appointment_id=None,
) -> list[dict[str, str]]:
    """캘린더 UI용 — 구간과 겹치는 확정 대면 예약 목록."""
    range_start = _calendar_localtime(range_start)
    range_end = _calendar_localtime(range_end)

    peers = Appointment.objects.filter(
        status=AppointmentStatus.CONFIRMED,
        case__counseling_method=CounselingMethod.IN_PERSON,
        scheduled_at__lt=range_end,
    )
    if exclude_appointment_id:
        peers = peers.exclude(pk=exclude_appointment_id)

    intervals: list[dict[str, str]] = []
    for appointment in peers.iterator():
        peer_start = _calendar_localtime(appointment.scheduled_at)
        peer_end = peer_start + timedelta(
            minutes=appointment_duration_minutes(appointment)
        )
        if peer_start < range_end and peer_end > range_start:
            intervals.append(
                {
                    "id": str(appointment.pk),
                    "start": peer_start.isoformat(),
                    "end": peer_end.isoformat(),
                }
            )
    return intervals
