"""예약 확정·변경 시 동시성 제어 — select_for_update."""

from __future__ import annotations

from datetime import date

from apps.counseling.models import CounselingMethod
from apps.reports.appointment_calendar import _calendar_localtime, calendar_day_bounds
from apps.scheduling.models import Appointment, AppointmentStatus


def _day_bounds_for_scheduled_at(scheduled_at) -> tuple:
    local_day = _calendar_localtime(scheduled_at).date()
    return calendar_day_bounds(local_day)


def lock_remote_scheduling_day(*, scheduled_at) -> None:
    """해당 날짜의 확정 비대면 예약 행을 잠금 (Zoom 호스트 풀 직렬화)."""
    day_start, day_end = _day_bounds_for_scheduled_at(scheduled_at)
    list(
        Appointment.objects.select_for_update()
        .filter(
            status=AppointmentStatus.CONFIRMED,
            case__counseling_method=CounselingMethod.REMOTE,
            scheduled_at__gte=day_start,
            scheduled_at__lt=day_end,
        )
        .order_by("pk")
    )


def lock_counselor_scheduling_day(*, counselor_id, scheduled_at) -> None:
    """해당 상담사·날짜의 활성 예약 행을 잠금 (상담사 시간 중복 직렬화)."""
    if not counselor_id:
        return
    day_start, day_end = _day_bounds_for_scheduled_at(scheduled_at)
    list(
        Appointment.objects.select_for_update()
        .filter(
            counselor_id=counselor_id,
            status__in=(
                AppointmentStatus.PENDING,
                AppointmentStatus.SCHEDULED,
                AppointmentStatus.CONFIRMED,
                AppointmentStatus.CANCEL_PENDING,
            ),
            scheduled_at__gte=day_start,
            scheduled_at__lt=day_end,
        )
        .order_by("pk")
    )


def acquire_scheduling_locks(*, counselor_id, scheduled_at) -> None:
    lock_remote_scheduling_day(scheduled_at=scheduled_at)
    lock_counselor_scheduling_day(counselor_id=counselor_id, scheduled_at=scheduled_at)
