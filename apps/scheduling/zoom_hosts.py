"""Zoom Licensed 사용자(호스트 풀) 조회·비대면 예약 호스트 배정."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.counseling.models import CounselingMethod
from apps.reports.appointment_calendar import (
    CalendarInterval,
    _calendar_localtime,
    assign_zoom_hosts,
)
from apps.scheduling.models import Appointment, AppointmentStatus

DEFAULT_ZOOM_LICENSED_USERS = (
    "sscukscu@gmail.com",
    "sedulife@mail.kcu.ac",
)


def get_zoom_licensed_user_emails() -> tuple[str, ...]:
    """Railway ZOOM_LICENSED_USERS 또는 기본 2명."""
    raw = (getattr(settings, "ZOOM_LICENSED_USERS", None) or "").strip()
    if raw:
        emails = tuple(e.strip() for e in raw.split(",") if e.strip())
        if emails:
            return emails
    return DEFAULT_ZOOM_LICENSED_USERS


def get_zoom_host_pool() -> tuple[str, ...]:
    """라이선스 사용자 수만큼 host_01, host_02, ..."""
    count = len(get_zoom_licensed_user_emails())
    if count < 1:
        return ("host_01",)
    return tuple(f"host_{index:02d}" for index in range(1, count + 1))


def host_id_for_email(email: str) -> str:
    normalized = (email or "").strip().lower()
    if not normalized:
        return ""
    for index, licensed in enumerate(get_zoom_licensed_user_emails(), start=1):
        if licensed.strip().lower() == normalized:
            return f"host_{index:02d}"
    return ""


def email_for_host_id(host_id: str) -> str:
    host_id = (host_id or "").strip()
    if not host_id.startswith("host_"):
        return ""
    try:
        index = int(host_id.removeprefix("host_")) - 1
    except ValueError:
        return ""
    emails = get_zoom_licensed_user_emails()
    if 0 <= index < len(emails):
        return emails[index]
    return ""


def _appointment_interval(appointment: Appointment) -> CalendarInterval:
    start_at = _calendar_localtime(appointment.scheduled_at)
    duration = appointment.duration_minutes or 50
    if duration <= 0:
        duration = 50
    end_at = start_at + timedelta(minutes=duration)
    is_remote = appointment.case.counseling_method == CounselingMethod.REMOTE
    return CalendarInterval(
        appointment_id=str(appointment.pk),
        start=start_at,
        end=end_at,
        is_remote=is_remote,
    )


def confirmed_remote_appointments_queryset():
    return (
        Appointment.objects.filter(
            status=AppointmentStatus.CONFIRMED,
            case__counseling_method=CounselingMethod.REMOTE,
        )
        .select_related("case", "client", "counselor", "zoom_meeting")
        .order_by("scheduled_at", "pk")
    )


def assign_host_emails_for_appointments(
    appointments: list[Appointment],
) -> dict[str, str]:
    """겹치는 비대면 확정 예약에 Licensed 이메일을 배정. {appointment_id: email}."""
    emails = get_zoom_licensed_user_emails()
    if not emails:
        return {}

    intervals = [_appointment_interval(apt) for apt in appointments]
    host_ids = assign_zoom_hosts(intervals)
    return {
        appointment_id: email_for_host_id(host_id) or emails[0]
        for appointment_id, host_id in host_ids.items()
    }


def resolve_zoom_host_email_for_appointment(appointment: Appointment) -> str:
    """단일 예약 생성 시 전체 확정 비대면 일정 기준 호스트 이메일."""
    emails = get_zoom_licensed_user_emails()
    if not emails:
        return ""

    peers = list(confirmed_remote_appointments_queryset())
    if appointment.pk and appointment not in peers:
        peers.append(appointment)
    elif appointment.pk:
        peers = [apt if apt.pk != appointment.pk else appointment for apt in peers]

    assignments = assign_host_emails_for_appointments(peers)
    return assignments.get(str(appointment.pk), emails[0])
