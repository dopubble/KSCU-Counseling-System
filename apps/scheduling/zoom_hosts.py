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
    intervals_conflict_with_buffer,
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


def buffer_overlapping_confirmed_remote_peers(
    *,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id=None,
) -> list[Appointment]:
    """80분 버퍼 윈도우와 겹치는 확정 비대면만 (전역 일정 간섭 제거)."""
    start = _calendar_localtime(scheduled_at)
    duration = duration_minutes or DEFAULT_APPOINTMENT_DURATION_MINUTES
    end = start + timedelta(minutes=duration)
    peers: list[Appointment] = []
    for peer in confirmed_remote_appointments_queryset():
        if exclude_appointment_id and peer.pk == exclude_appointment_id:
            continue
        peer_start = _calendar_localtime(peer.scheduled_at)
        peer_duration = peer.duration_minutes or DEFAULT_APPOINTMENT_DURATION_MINUTES
        peer_end = peer_start + timedelta(minutes=peer_duration)
        if intervals_conflict_with_buffer(start, end, peer_start, peer_end):
            peers.append(peer)
    return peers


def assign_host_emails_for_appointments(
    appointments: list[Appointment],
) -> dict[str, str]:
    """
    겹치는 비대면 예약에 Licensed 이메일 배정.

    확정 순(confirmed_at) → 같은 순서면 상담 시각 → pk 순으로
    빈 호스트를 배정한다. 버퍼 안에서 호스트가 없으면 해당 건은 결과에 없음(예약 불가).
    Licensed 풀 외(hakyss 등) 저장 호스트는 이 함수에서 건너뛴다.
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

    def _order_key(pair: tuple) -> tuple:
        apt, interval = pair
        if apt.status == AppointmentStatus.CONFIRMED and apt.confirmed_at:
            return (apt.confirmed_at, interval.start, apt.pk or 0)
        return (_SORT_LAST, interval.start, apt.pk or 0)

    for apt, interval in sorted(remote_pairs, key=_order_key):
        zoom = getattr(apt, "zoom_meeting", None)
        stored = (zoom.zoom_host_email or "").strip().lower() if zoom else ""
        if stored and stored not in licensed_set:
            continue

        occ_end = _occupied_end(interval)
        assigned_host: str | None = None
        for host in pool:
            if _host_is_free(host, interval.start, occ_end):
                assigned_host = host
                break
        if assigned_host is None:
            # 이미 확정·Zoom 생성된 건은 DB 호스트로 점유를 남겨야 이후 배정이 host_01을 중복 사용하지 않음
            if (
                apt.status == AppointmentStatus.CONFIRMED
                and stored
                and stored in licensed_set
            ):
                pinned_id = host_id_for_email(stored)
                if pinned_id:
                    _reserve(pinned_id, interval)
            continue
        _reserve(assigned_host, interval)
        email = email_for_host_id(assigned_host)
        if email:
            result[str(apt.pk)] = email

    return result


def remote_slot_candidate(
    candidate_key: str,
    *,
    scheduled_at: datetime,
    duration_minutes: int,
):
    """용량 검사용 미저장 비대면 후보(확정 순서상 마지막)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        pk=candidate_key,
        status=AppointmentStatus.PENDING,
        confirmed_at=None,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        case=SimpleNamespace(counseling_method=CounselingMethod.REMOTE),
        zoom_meeting=None,
    )


def resolve_zoom_host_email_for_appointment(appointment: Appointment) -> str:
    """단일 예약 생성 시 버퍼 겹치는 확정 비대면 + 본인 기준 호스트 이메일."""
    emails = get_zoom_licensed_user_emails()
    if not emails:
        return ""

    duration = appointment.duration_minutes or DEFAULT_APPOINTMENT_DURATION_MINUTES
    peers = buffer_overlapping_confirmed_remote_peers(
        scheduled_at=appointment.scheduled_at,
        duration_minutes=duration,
        exclude_appointment_id=appointment.pk,
    )
    if appointment.pk and appointment not in peers:
        peers.append(appointment)

    assignments = assign_host_emails_for_appointments(peers)
    return assignments.get(str(appointment.pk), "")
