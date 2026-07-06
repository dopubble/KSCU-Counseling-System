"""비대면(Zoom) 동시 예약 용량 — 동시간대 상한·호스트 풀·30분 버퍼."""

from __future__ import annotations

from datetime import datetime, timedelta

from apps.counseling.models import CounselingMethod
from apps.reports.appointment_calendar import (
    CalendarInterval,
    _calendar_localtime,
    assign_zoom_hosts,
)
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.zoom_hosts import get_zoom_licensed_user_emails
from apps.scheduling.zoom_scheduling_settings import (
    get_remote_zoom_simultaneous_capacity,
    remote_zoom_host_pool_size,
)

REMOTE_ZOOM_CAPACITY_FULL_MESSAGE = (
    "해당 시간대 비대면 상담 예약이 만석입니다. 다른 시간을 선택해 주세요."
)


def appointment_duration_minutes(appointment: Appointment) -> int:
    duration = appointment.duration_minutes or DEFAULT_APPOINTMENT_DURATION_MINUTES
    return duration if duration > 0 else DEFAULT_APPOINTMENT_DURATION_MINUTES


def remote_zoom_capacity_limit() -> int:
    """예약 UI·API — 같은 시작 시각 동시 확정 상한 (기본 2, 관리자 조정 가능)."""
    return get_remote_zoom_simultaneous_capacity()


def _slot_start_key(dt: datetime) -> datetime:
    return _calendar_localtime(dt).replace(second=0, microsecond=0)


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


def count_same_start_confirmed_remote(
    *,
    scheduled_at: datetime,
    exclude_appointment_id=None,
) -> int:
    """같은 시작 시각(분 단위) 확정 비대면 예약 수."""
    target = _slot_start_key(scheduled_at)
    return sum(
        1
        for peer in _confirmed_remote_intervals(
            exclude_appointment_id=exclude_appointment_id
        )
        if _slot_start_key(peer.start) == target
    )


def count_overlapping_confirmed_remote(
    *,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id=None,
) -> int:
    """버퍼 포함 겹침 수 — 레거시·잔여 좌석 추정용."""
    from apps.reports.appointment_calendar import intervals_conflict_with_buffer

    start = _calendar_localtime(scheduled_at)
    end = start + timedelta(minutes=duration_minutes)
    count = 0
    for peer in _confirmed_remote_intervals(exclude_appointment_id=exclude_appointment_id):
        if intervals_conflict_with_buffer(start, end, peer.start, peer.end):
            count += 1
    return count


def remote_zoom_same_start_remaining(
    *,
    scheduled_at: datetime,
    exclude_appointment_id=None,
) -> int:
    """같은 시작 시각 기준 남은 동시 확정 슬롯."""
    cap = remote_zoom_capacity_limit()
    used = count_same_start_confirmed_remote(
        scheduled_at=scheduled_at,
        exclude_appointment_id=exclude_appointment_id,
    )
    return max(0, cap - used)


def zoom_host_assignable_for_slot(
    *,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id=None,
    candidate_id: str | None = None,
) -> tuple[bool, str | None]:
    """
    신규·변경 슬롯에 배정 가능한 Zoom 호스트가 있는지.

    1) 같은 시작 시각 확정 건수가 simultaneous 상한 이상이면 불가.
    2) Licensed 전체 호스트 풀(host_03 포함)로 버퍼 포함 배정 시도.
    """
    if remote_zoom_host_pool_size() <= 0:
        return True, None

    start = _calendar_localtime(scheduled_at)
    end = start + timedelta(minutes=duration_minutes)
    candidate_key = candidate_id or _candidate_interval_id(
        appointment_id=exclude_appointment_id,
        scheduled_at=start,
    )

    simultaneous = remote_zoom_capacity_limit()
    same_start = count_same_start_confirmed_remote(
        scheduled_at=start,
        exclude_appointment_id=exclude_appointment_id,
    )
    if same_start >= simultaneous:
        return False, None

    peers = _confirmed_remote_intervals(exclude_appointment_id=exclude_appointment_id)
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


def is_remote_zoom_slot_available(
    *,
    scheduled_at: datetime,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    exclude_appointment_id=None,
) -> bool:
    ok, _host_id = zoom_host_assignable_for_slot(
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    )
    return ok


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
