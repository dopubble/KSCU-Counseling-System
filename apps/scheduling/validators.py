"""예약 모델·서비스 공통 검증."""

from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.counseling.models import CounselingMethod
from apps.scheduling.models import AppointmentStatus
from apps.scheduling.zoom_capacity import (
    REMOTE_ZOOM_CAPACITY_FULL_MESSAGE,
    appointment_duration_minutes,
    is_remote_zoom_buffer_slot_available,
)


def validate_remote_zoom_concurrency(appointment) -> None:
    """
    비대면 확정 예약 — 50분 상담 + 30분 버퍼 범위 내 동시 REMOTE 상한 검사.
    대면·취소·대기 예약은 검사하지 않는다.
    """
    if appointment.case.counseling_method != CounselingMethod.REMOTE:
        return
    if appointment.status not in (
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.SCHEDULED,
    ):
        return
    if not appointment.scheduled_at:
        return

    duration = appointment_duration_minutes(appointment)
    if is_remote_zoom_buffer_slot_available(
        scheduled_at=appointment.scheduled_at,
        duration_minutes=duration,
        exclude_appointment_id=appointment.pk,
    ):
        return
    raise ValidationError({"scheduled_at": REMOTE_ZOOM_CAPACITY_FULL_MESSAGE})
