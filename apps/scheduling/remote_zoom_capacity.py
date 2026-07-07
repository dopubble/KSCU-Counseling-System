"""비대면(Zoom) 동시 예약 용량 — zoom_capacity + 호스트 배정 조합."""

from __future__ import annotations

from datetime import datetime

from apps.counseling.models import CounselingMethod
from apps.reports.appointment_calendar import _calendar_localtime
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment
from apps.scheduling.zoom_capacity import (
    REMOTE_ZOOM_CAPACITY_FULL_MESSAGE,
    appointment_duration_minutes,
    check_remote_zoom_buffer_capacity,
    count_buffer_overlapping_confirmed_remote,
    is_remote_zoom_buffer_slot_available,
    remote_zoom_buffer_overlapping_remaining,
    remote_zoom_capacity_limit,
    remote_zoom_licensed_slot_limit,
)
from apps.scheduling.zoom_hosts import (
    assign_host_emails_for_appointments,
    buffer_overlapping_confirmed_remote_peers,
    host_id_for_email,
    remote_slot_candidate,
)

# 하위 호환 re-export
__all__ = [
    "REMOTE_ZOOM_CAPACITY_FULL_MESSAGE",
    "appointment_duration_minutes",
    "remote_zoom_capacity_limit",
    "remote_zoom_licensed_slot_limit",
    "count_buffer_overlapping_confirmed_remote",
    "count_overlapping_confirmed_remote",
    "remote_zoom_buffer_overlapping_remaining",
    "remote_zoom_same_start_remaining",
    "zoom_host_assignable_for_slot",
    "is_remote_zoom_slot_available",
    "check_remote_zoom_capacity",
    "ensure_remote_zoom_capacity",
    "get_remote_zoom_busy_intervals",
]


def count_overlapping_confirmed_remote(
    *,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id=None,
) -> int:
    """버퍼 포함 겹침 수 (zoom_capacity 단일 규칙)."""
    return count_buffer_overlapping_confirmed_remote(
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    )


def remote_zoom_same_start_remaining(
    *,
    scheduled_at: datetime,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    exclude_appointment_id=None,
) -> int:
    """슬롯 UI — 버퍼 포함 겹침 기준 남은 좌석 (하위 호환 함수명)."""
    return remote_zoom_buffer_overlapping_remaining(
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    )


def _candidate_interval_id(*, appointment_id, scheduled_at: datetime) -> str:
    if appointment_id:
        return str(appointment_id)
    return f"candidate:{scheduled_at.isoformat()}"


def zoom_host_assignable_for_slot(
    *,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id=None,
    candidate_id: str | None = None,
) -> tuple[bool, str | None]:
    """Licensed 호스트 풀에 배정 가능한지 (용량 통과 후 호스트 배정)."""
    from apps.scheduling.zoom_scheduling_settings import remote_zoom_host_pool_size

    if remote_zoom_host_pool_size() <= 0:
        return True, None

    start = _calendar_localtime(scheduled_at)
    candidate_key = candidate_id or _candidate_interval_id(
        appointment_id=exclude_appointment_id,
        scheduled_at=start,
    )

    peers = buffer_overlapping_confirmed_remote_peers(
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    )

    candidate = remote_slot_candidate(
        candidate_key,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    assignments = assign_host_emails_for_appointments(peers + [candidate])
    email = (assignments.get(candidate_key) or "").strip()
    if not email:
        return False, None
    return True, host_id_for_email(email)


def is_remote_zoom_slot_available(
    *,
    scheduled_at: datetime,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    exclude_appointment_id=None,
) -> bool:
    """① 80분 버퍼 윈도우 내 REMOTE < limit  ② Licensed 호스트 배정 가능."""
    if not is_remote_zoom_buffer_slot_available(
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    ):
        return False
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
    """비대면 예약 용량 + 호스트 배정 가능 여부."""
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

    ok, message = check_remote_zoom_buffer_capacity(
        scheduled_at=when,
        duration_minutes=duration,
        exclude_appointment_id=exclude,
    )
    if not ok:
        return False, message

    host_ok, _host_id = zoom_host_assignable_for_slot(
        scheduled_at=when,
        duration_minutes=duration,
        exclude_appointment_id=exclude,
        candidate_id=str(exclude) if exclude else None,
    )
    if not host_ok:
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
    from apps.scheduling.zoom_capacity import _confirmed_remote_intervals

    range_start = _calendar_localtime(range_start)
    range_end = _calendar_localtime(range_end)

    intervals: list[dict[str, str]] = []
    for peer in _confirmed_remote_intervals(
        exclude_appointment_id=exclude_appointment_id
    ):
        if peer.start < range_end and peer.end > range_start:
            intervals.append(
                {
                    "id": peer.appointment_id,
                    "start": peer.start.isoformat(),
                    "end": peer.end.isoformat(),
                }
            )
    return intervals
