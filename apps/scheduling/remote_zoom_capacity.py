"""비대면(Zoom) 동시 예약 용량 — Licensed 호스트 수·30분 버퍼 반영."""

from __future__ import annotations

from datetime import datetime, timedelta

from apps.counseling.models import CounselingMethod
from apps.reports.appointment_calendar import (
    CalendarInterval,
    _calendar_localtime,
    assign_zoom_hosts,
    intervals_conflict_with_buffer,
)
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


def _candidate_interval_id(*, appointment_id, scheduled_at: datetime) -> str:
    if appointment_id:
        return str(appointment_id)
    return f"candidate:{scheduled_at.isoformat()}"


def _confirmed_remote_intervals(
    *,
    exclude_appointment_id=None,
) -> list[CalendarInterval]:
    peers = Appointment.objects.filter(
        status=AppointmentStatus.CONFIRMED,
        case__counseling_method=CounselingMethod.REMOTE,
    ).select_related("case")
    if exclude_appointment_id:
        peers = peers.exclude(pk=exclude_appointment_id)

    intervals: list[CalendarInterval] = []
    for appointment in peers.iterator():
        start = _calendar_localtime(appointment.scheduled_at)
        end = start + timedelta(minutes=appointment_duration_minutes(appointment))
        intervals.append(
            CalendarInterval(
                appointment_id=str(appointment.pk),
                start=start,
                end=end,
                is_remote=True,
            )
        )
    return intervals


def count_overlapping_confirmed_remote(
    *,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id=None,
) -> int:
    """버퍼 포함 겹침 — 기존 API 호환용 (슬롯 UI 잔여 좌석 추정)."""
    start = _calendar_localtime(scheduled_at)
    end = start + timedelta(minutes=duration_minutes)
    count = 0
    for peer in _confirmed_remote_intervals(exclude_appointment_id=exclude_appointment_id):
        if intervals_conflict_with_buffer(start, end, peer.start, peer.end):
            count += 1
    return count


def zoom_host_assignable_for_slot(
    *,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id=None,
    candidate_id: str | None = None,
) -> tuple[bool, str | None]:
    """신규·변경 슬롯에 배정 가능한 Zoom 호스트가 있는지 (30분 버퍼 포함)."""
    limit = remote_zoom_capacity_limit()
    if limit <= 0:
        return True, None

    start = _calendar_localtime(scheduled_at)
    end = start + timedelta(minutes=duration_minutes)
    candidate_key = candidate_id or _candidate_interval_id(
        appointment_id=exclude_appointment_id,
        scheduled_at=start,
    )
    peers = _confirmed_remote_intervals(exclude_appointment_id=exclude_appointment_id)

    conflicts = sum(
        1
        for peer in peers
        if intervals_conflict_with_buffer(start, end, peer.start, peer.end)
    )
    if conflicts >= limit:
        return False, None

    intervals = list(peers)
    intervals.append(
        CalendarInterval(
            appointment_id=candidate_key,
            start=start,
            end=end,
            is_remote=True,
        )
    )
    assignments = assign_zoom_hosts(intervals)
    host_id = assignments.get(candidate_key)
    if host_id:
        return True, host_id
    return False, None


def check_remote_zoom_capacity(
    appointment: Appointment,
    *,
    scheduled_at: datetime | None = None,
    duration_minutes: int | None = None,
    exclude_appointment_id=None,
) -> tuple[bool, str]:
    """비대면 예약이 Zoom 호스트 풀(버퍼 포함) 내인지 확인."""
    if appointment.case.counseling_method != CounselingMethod.REMOTE:
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
    ok, _host_id = zoom_host_assignable_for_slot(
        scheduled_at=when,
        duration_minutes=duration,
        exclude_appointment_id=exclude,
        candidate_id=str(exclude) if exclude else None,
    )
    if not ok:
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

    intervals: list[dict[str, str]] = []
    for peer in _confirmed_remote_intervals(exclude_appointment_id=exclude_appointment_id):
        if peer.start < range_end and peer.end > range_start:
            intervals.append(
                {
                    "id": peer.appointment_id,
                    "start": peer.start.isoformat(),
                    "end": peer.end.isoformat(),
                }
            )
    return intervals
