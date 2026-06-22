"""비대면(Zoom) 동시 예약 용량 — Licensed 호스트 수만큼 겹치는 확정 예약 제한."""

from __future__ import annotations

from datetime import datetime, timedelta

from apps.counseling.models import CounselingMethod
from apps.reports.appointment_calendar import _calendar_localtime, _intervals_overlap
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.zoom_hosts import get_zoom_licensed_user_emails

REMOTE_ZOOM_CAPACITY_FULL_MESSAGE = (
    "해당 시간대 비대면 상담 예약이 만석입니다. 다른 시간을 선택해 주세요."
)


def appointment_duration_minutes(appointment: Appointment) -> int:
    duration = appointment.duration_minutes or DEFAULT_APPOINTMENT_DURATION_MINUTES
    return duration if duration > 0 else DEFAULT_APPOINTMENT_DURATION_MINUTES


def remote_zoom_capacity_limit() -> int:
    return len(get_zoom_licensed_user_emails())


def count_overlapping_confirmed_remote(
    *,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id=None,
) -> int:
    """겹치는 확정 비대면 예약 수 (기존시작 < 새종료 AND 기존종료 > 새시작)."""
    start = _calendar_localtime(scheduled_at)
    end = start + timedelta(minutes=duration_minutes)

    peers = Appointment.objects.filter(
        status=AppointmentStatus.CONFIRMED,
        case__counseling_method=CounselingMethod.REMOTE,
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


def check_remote_zoom_capacity(
    appointment: Appointment,
    *,
    scheduled_at: datetime | None = None,
    duration_minutes: int | None = None,
    exclude_appointment_id=None,
) -> tuple[bool, str]:
    """비대면 예약이 Zoom 동시 용량 내인지 확인."""
    if appointment.case.counseling_method != CounselingMethod.REMOTE:
        return True, ""

    limit = remote_zoom_capacity_limit()
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
    overlap_count = count_overlapping_confirmed_remote(
        scheduled_at=when,
        duration_minutes=duration,
        exclude_appointment_id=exclude,
    )
    if overlap_count >= limit:
        return False, REMOTE_ZOOM_CAPACITY_FULL_MESSAGE
    return True, ""


def ensure_remote_zoom_capacity(
    appointment: Appointment,
    *,
    scheduled_at: datetime | None = None,
    duration_minutes: int | None = None,
    exclude_appointment_id=None,
) -> None:
    ok, message = check_remote_zoom_capacity(
        appointment,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    )
    if not ok:
        from apps.scheduling.services import AppointmentServiceError

        raise AppointmentServiceError(message)


def get_remote_zoom_busy_intervals(
    range_start: datetime,
    range_end: datetime,
    *,
    exclude_appointment_id=None,
) -> list[dict[str, str]]:
    """캘린더 UI용 — 구간과 겹치는 확정 비대면 예약 목록."""
    range_start = _calendar_localtime(range_start)
    range_end = _calendar_localtime(range_end)

    peers = Appointment.objects.filter(
        status=AppointmentStatus.CONFIRMED,
        case__counseling_method=CounselingMethod.REMOTE,
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
