"""상담 취소·변경 운영 규칙 (24시간 전/당일 취소 누적)."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.scheduling.models import Appointment, AppointmentStatus

CANCELLATION_LOCK_HOURS = 24
EARLY_CLOSE_DAY_CANCEL_THRESHOLD = 3


class AppointmentOperationError(Exception):
    """예약 취소·변경 정책 위반."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def get_appointment_datetime(appointment: Appointment):
    """상담 예약 일시 (appointment_datetime 별칭)."""
    return appointment.appointment_datetime


def time_until_appointment(appointment: Appointment) -> timedelta:
    return get_appointment_datetime(appointment) - timezone.now()


def is_appointment_in_past(appointment: Appointment) -> bool:
    return time_until_appointment(appointment) <= timedelta(0)


def is_within_24h_lock_window(appointment: Appointment) -> bool:
    """상담 예정 시각까지 24시간 미만 남은 경우(아직 시작 전)."""
    delta = time_until_appointment(appointment)
    return timedelta(0) < delta < timedelta(hours=CANCELLATION_LOCK_HOURS)


def client_change_blocked(appointment: Appointment) -> bool:
    """확정 예약 — 24시간 이내·이미 지난 예약 변경 불가. 대기(PENDING)는 제한 없음."""
    if appointment.status == AppointmentStatus.PENDING:
        return False
    return is_appointment_in_past(appointment) or is_within_24h_lock_window(appointment)


def client_cancel_blocked(appointment: Appointment) -> bool:
    """취소 요청 불가(이미 지난 예약만). 24시간 이내는 패널티 후 허용."""
    return is_appointment_in_past(appointment)


def cancel_triggers_session_penalty(appointment: Appointment) -> bool:
    """24시간 이내 취소 시 회기 1회 차감."""
    return is_within_24h_lock_window(appointment)


def is_same_day_as_appointment(appointment: Appointment, *, at=None) -> bool:
    """예약 당일(로컬 날짜 기준) 여부."""
    at = at or timezone.now()
    scheduled_local = timezone.localtime(get_appointment_datetime(appointment))
    at_local = timezone.localtime(at)
    return scheduled_local.date() == at_local.date()


def policy_messages(appointment: Appointment) -> dict[str, str]:
    """템플릿용 안내 문구."""
    if appointment.status == AppointmentStatus.PENDING:
        return {"change": "", "cancel": ""}
    if is_appointment_in_past(appointment):
        return {
            "change": "이미 지난 상담 예약은 변경할 수 없습니다.",
            "cancel": "이미 지난 상담 예약은 취소할 수 없습니다.",
        }
    if is_within_24h_lock_window(appointment):
        return {
            "change": "상담 예정일 24시간 이내에는 예약 변경이 불가합니다.",
            "cancel": (
                "상담 24시간 이내 취소 시 남은 상담 회기 1회가 차감됩니다. "
                "당일 취소가 누적되면 조기 종결될 수 있습니다."
            ),
        }
    return {"change": "", "cancel": ""}
