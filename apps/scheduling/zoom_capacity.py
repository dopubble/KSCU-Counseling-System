"""비대면 Zoom 동시 예약 용량 — 50분 상담 + 30분 버퍼(80분 점유) 내 최대 N건."""

from __future__ import annotations

from datetime import datetime, timedelta

from apps.counseling.models import CounselingMethod
from apps.reports.appointment_calendar import (
    CalendarInterval,
    _calendar_localtime,
    intervals_conflict_with_buffer,
)
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.zoom_hosts import get_zoom_licensed_user_emails
from apps.scheduling.zoom_scheduling_settings import get_remote_zoom_simultaneous_capacity

REMOTE_ZOOM_CAPACITY_FULL_MESSAGE = (
    "해당 시간대 비대면 상담 예약이 만석입니다. 다른 시간을 선택해 주세요."
)


def appointment_duration_minutes(appointment: Appointment) -> int:
    duration = appointment.duration_minutes or DEFAULT_APPOINTMENT_DURATION_MINUTES
    return duration if duration > 0 else DEFAULT_APPOINTMENT_DURATION_MINUTES


def remote_zoom_licensed_slot_limit() -> int:
    """Licensed 호스트 수와 관리자 동시 상한 중 작은 값 (기본 2)."""
    pool_size = len(get_zoom_licensed_user_emails())
    admin_cap = get_remote_zoom_simultaneous_capacity()
    if pool_size <= 0:
        return max(1, admin_cap)
    return min(pool_size, admin_cap)


def remote_zoom_capacity_limit() -> int:
    """하위 호환 — remote_zoom_licensed_slot_limit 과 동일."""
    return remote_zoom_licensed_slot_limit()


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


def count_buffer_overlapping_confirmed_remote(
    *,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id=None,
) -> int:
    """30분 버퍼 포함 겹치는 확정 비대면(REMOTE) 예약 수."""
    start = _calendar_localtime(scheduled_at)
    end = start + timedelta(minutes=duration_minutes)
    count = 0
    for peer in _confirmed_remote_intervals(
        exclude_appointment_id=exclude_appointment_id
    ):
        if intervals_conflict_with_buffer(start, end, peer.start, peer.end):
            count += 1
    return count


def remote_zoom_buffer_overlapping_remaining(
    *,
    scheduled_at: datetime,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    exclude_appointment_id=None,
) -> int:
    """버퍼 포함 겹침 기준 남은 비대면 슬롯."""
    limit = remote_zoom_licensed_slot_limit()
    used = count_buffer_overlapping_confirmed_remote(
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    )
    return max(0, limit - used)


def is_remote_zoom_buffer_slot_available(
    *,
    scheduled_at: datetime,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    exclude_appointment_id=None,
) -> bool:
    """80분 점유(50+30 버퍼) 윈도우 내 확정 비대면이 limit 미만이면 True."""
    limit = remote_zoom_licensed_slot_limit()
    used = count_buffer_overlapping_confirmed_remote(
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    )
    return used < limit


def check_remote_zoom_buffer_capacity(
    *,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id=None,
) -> tuple[bool, str]:
    if is_remote_zoom_buffer_slot_available(
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    ):
        return True, ""
    return False, REMOTE_ZOOM_CAPACITY_FULL_MESSAGE
