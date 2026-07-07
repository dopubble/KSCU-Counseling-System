"""Zoom Licensed 사용자(호스트 풀) 조회·비대면 예약 호스트 배정."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

from apps.counseling.models import CounselingMethod
from apps.reports.appointment_calendar import (
    CalendarInterval,
    _calendar_localtime,
    _intervals_overlap,
    get_zoom_host_buffer_minutes,
)
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus

DEFAULT_ZOOM_LICENSED_USERS = (
    "sscukscu@gmail.com",
    "sedulife@mail.kcu.ac",
)

_SORT_LAST = datetime(9999, 12, 31, tzinfo=dt_timezone.utc)


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
    duration = appointment.duration_minutes or DEFAULT_APPOINTMENT_DURATION_MINUTES
    if duration <= 0:
        duration = DEFAULT_APPOINTMENT_DURATION_MINUTES
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
    """
    겹치는 비대면 예약에 Licensed 이메일 배정.

    1) 확정 순(confirmed_at)으로 DB 저장 호스트를 우선 점유(먼저 잡은 회기 유지)
    2) 나머지는 상담 시각 순 → 같은 시각이면 확정 빠른 순으로 빈 호스트 배정
    """
    licensed_emails = get_zoom_licensed_user_emails()
    if not licensed_emails:
        return {}
    licensed_set = {email.strip().lower() for email in licensed_emails if email.strip()}

    pool = get_zoom_host_pool()
    buffer = timedelta(minutes=get_zoom_host_buffer_minutes())
    host_schedules: dict[str, list[tuple[datetime, datetime]]] = {
        host: [] for host in pool
    }
    result: dict[str, str] = {}

    remote_pairs = [
        (apt, _appointment_interval(apt))
        for apt in appointments
        if _appointment_interval(apt).is_remote
    ]

    def _occupied_end(interval: CalendarInterval) -> datetime:
        return interval.end + buffer

    def _host_is_free(host_id: str, start: datetime, occupied_end: datetime) -> bool:
        return all(
            not _intervals_overlap(start, occupied_end, busy_start, busy_end)
            for busy_start, busy_end in host_schedules[host_id]
        )

    def _reserve(host_id: str, interval: CalendarInterval) -> None:
        host_schedules[host_id].append((interval.start, _occupied_end(interval)))

    # Pass 1 — 먼저 확정된 예약의 저장 호스트 유지
    pin_order = sorted(
        remote_pairs,
        key=lambda pair: (
            pair[0].confirmed_at or _SORT_LAST,
            pair[1].start,
            pair[0].pk,
        ),
    )
    for apt, interval in pin_order:
        if apt.status != AppointmentStatus.CONFIRMED:
            continue
        zoom = getattr(apt, "zoom_meeting", None)
        stored = (zoom.zoom_host_email or "").strip().lower() if zoom else ""
        if not stored or stored not in licensed_set:
            continue
        host_id = host_id_for_email(stored)
        if not host_id:
            continue
        occ_end = _occupied_end(interval)
        if _host_is_free(host_id, interval.start, occ_end):
            _reserve(host_id, interval)
            result[str(apt.pk)] = stored

    # Pass 2 — 미배정 건: 상담 시각 순 배정
    assign_order = sorted(
        remote_pairs,
        key=lambda pair: (
            pair[1].start,
            pair[0].confirmed_at or _SORT_LAST,
            pair[0].pk,
        ),
    )
    for apt, interval in assign_order:
        pk = str(apt.pk)
        if pk in result:
            continue
        occ_end = _occupied_end(interval)
        assigned_host: str | None = None
        for host in pool:
            if _host_is_free(host, interval.start, occ_end):
                assigned_host = host
                break
        if assigned_host is None:
            continue
        _reserve(assigned_host, interval)
        email = email_for_host_id(assigned_host)
        if email:
            result[pk] = email

    return result


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
    return assignments.get(str(appointment.pk), "")
