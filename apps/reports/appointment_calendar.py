"""관리자 예약 캘린더 — FullCalendar 이벤트 직렬화·Zoom 호스트 분배 표시."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.counseling.models import CounselingMethod
from apps.scheduling.models import Appointment, AppointmentStatus

logger = logging.getLogger(__name__)

DEFAULT_ZOOM_HOST_POOL = ("host_01", "host_02")

HOST_COLORS: dict[str, dict[str, str]] = {
    "host_01": {"bg": "#4f46e5", "border": "#3730a3"},
    "host_02": {"bg": "#059669", "border": "#047857"},
    "host_03": {"bg": "#d97706", "border": "#b45309"},
    "host_04": {"bg": "#db2777", "border": "#be185d"},
}

IN_PERSON_COLORS = {"bg": "#64748b", "border": "#475569"}
REMOTE_NO_ZOOM_COLORS = {"bg": "#0891b2", "border": "#0e7490"}

# DB 선필터용 — 최장 상담 시간보다 넉넉한 버퍼(분)
CALENDAR_RANGE_BUFFER_MINUTES = 180


@dataclass(frozen=True)
class CalendarInterval:
    appointment_id: str
    start: datetime
    end: datetime
    is_remote: bool


def get_calendar_timezone_name() -> str:
    return getattr(settings, "TIME_ZONE", "Asia/Seoul")


def get_zoom_host_pool() -> tuple[str, ...]:
    raw = getattr(settings, "ZOOM_HOST_POOL", "") or ""
    hosts = tuple(h.strip() for h in raw.split(",") if h.strip())
    return hosts or DEFAULT_ZOOM_HOST_POOL


def zoom_host_label(host_id: str) -> str:
    if host_id.startswith("host_"):
        suffix = host_id.removeprefix("host_").lstrip("0") or "0"
        try:
            return f"Zoom 호스트 {int(suffix)}번"
        except ValueError:
            pass
    return host_id


def _host_colors(host_id: str) -> dict[str, str]:
    if host_id in HOST_COLORS:
        return HOST_COLORS[host_id]
    idx = hash(host_id) % len(HOST_COLORS)
    return list(HOST_COLORS.values())[idx]


def _intervals_overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def _calendar_service_tz() -> ZoneInfo:
    return ZoneInfo(get_calendar_timezone_name())


def _calendar_localtime(value: datetime) -> datetime:
    """캘린더 구간 비교·표시는 서비스 타임존(Asia/Seoul) 기준."""
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return timezone.localtime(value, _calendar_service_tz())


def parse_calendar_bound(raw: str) -> datetime | None:
    """FullCalendar start/end 쿼리를 Asia/Seoul 기준 aware datetime으로 변환."""
    text = (raw or "").strip()
    if not text:
        return None
    parsed = parse_datetime(text)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, _calendar_service_tz())
    return _calendar_localtime(parsed)


def calendar_day_bounds(local_day: date) -> tuple[datetime, datetime]:
    """로컬 날짜 하루 구간 [00:00, 다음날 00:00) — FullCalendar day view와 동일."""
    service_tz = _calendar_service_tz()
    start = timezone.make_aware(datetime.combine(local_day, time.min), service_tz)
    end = start + timedelta(days=1)
    return start, end


def appointment_in_calendar_events(
    appointment_id,
    *,
    local_day: date,
) -> bool:
    """해당 날짜 day view 범위에 예약 이벤트가 포함되는지."""
    start, end = calendar_day_bounds(local_day)
    event_ids = {event["id"] for event in build_calendar_events(start=start, end=end)}
    return str(appointment_id) in event_ids


def _db_query_bounds(
    start: datetime | None,
    end: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    """FullCalendar 구간을 PostgreSQL timestamptz 비교용 UTC 경계로 변환."""
    query_start = query_end = None
    if start is not None:
        query_start = _calendar_localtime(start) - timedelta(
            minutes=CALENDAR_RANGE_BUFFER_MINUTES
        )
        query_start = query_start.astimezone(dt_timezone.utc)
    if end is not None:
        query_end = _calendar_localtime(end) + timedelta(
            minutes=CALENDAR_RANGE_BUFFER_MINUTES
        )
        query_end = query_end.astimezone(dt_timezone.utc)
    return query_start, query_end


def appointment_overlaps_range(
    start_at: datetime,
    end_at: datetime,
    *,
    range_start: datetime | None,
    range_end: datetime | None,
) -> bool:
    """예약 구간 [start_at, end_at)이 캘린더 표시 구간과 겹치는지."""
    start_at = _calendar_localtime(start_at)
    end_at = _calendar_localtime(end_at)
    range_start = _calendar_localtime(range_start) if range_start is not None else None
    range_end = _calendar_localtime(range_end) if range_end is not None else None

    if range_start is None and range_end is None:
        return True
    if range_end is not None and start_at >= range_end:
        return False
    if range_start is not None and end_at <= range_start:
        return False
    return True


def assign_zoom_hosts(intervals: list[CalendarInterval]) -> dict[str, str]:
    """겹치는 비대면 확정 예약에 호스트 풀을 순차 배정 (표시용)."""
    pool = get_zoom_host_pool()
    remote_intervals = sorted(
        [item for item in intervals if item.is_remote],
        key=lambda item: (item.start, item.appointment_id),
    )
    host_schedules: dict[str, list[tuple[datetime, datetime]]] = {host: [] for host in pool}
    assignments: dict[str, str] = {}

    for item in remote_intervals:
        assigned = pool[0]
        for host in pool:
            busy = host_schedules[host]
            if all(
                not _intervals_overlap(item.start, item.end, start, end)
                for start, end in busy
            ):
                assigned = host
                break
        host_schedules[assigned].append((item.start, item.end))
        assignments[item.appointment_id] = assigned

    return assignments


def get_mock_calendar_events(*, base_date: datetime | None = None) -> list[dict[str, Any]]:
    """개발·데모용 가상 일정 (요구사항 예시 구조)."""
    base = timezone.localtime(base_date or timezone.now())
    day = base.replace(hour=0, minute=0, second=0, microsecond=0)
    if base.weekday() >= 5:
        day += timedelta(days=(7 - base.weekday()))

    samples = [
        {
            "id": "mock-1",
            "title": "김내담 (3회차)",
            "start": (day + timedelta(hours=14)).isoformat(),
            "end": (day + timedelta(hours=15)).isoformat(),
            "counselor": "박상담",
            "client_name": "김내담",
            "session_number": 3,
            "zoom_host_id": "host_01",
            "zoom_url": "https://zoom.us/j/1234567890",
            "counseling_method": CounselingMethod.REMOTE,
            "status": AppointmentStatus.CONFIRMED,
            "case_number": "CASE-MOCK-001",
        },
        {
            "id": "mock-2",
            "title": "이내담 (1회차)",
            "start": (day + timedelta(hours=14, minutes=30)).isoformat(),
            "end": (day + timedelta(hours=15, minutes=30)).isoformat(),
            "counselor": "최상담",
            "client_name": "이내담",
            "session_number": 1,
            "zoom_host_id": "host_02",
            "zoom_url": "https://zoom.us/j/0987654321",
            "counseling_method": CounselingMethod.REMOTE,
            "status": AppointmentStatus.CONFIRMED,
            "case_number": "CASE-MOCK-002",
        },
        {
            "id": "mock-3",
            "title": "박내담 (2회차)",
            "start": (day + timedelta(days=1, hours=11)).isoformat(),
            "end": (day + timedelta(days=1, hours=11, minutes=50)).isoformat(),
            "counselor": "정상담",
            "client_name": "박내담",
            "session_number": 2,
            "zoom_host_id": "",
            "zoom_url": "",
            "counseling_method": CounselingMethod.IN_PERSON,
            "status": AppointmentStatus.CONFIRMED,
            "case_number": "CASE-MOCK-003",
        },
    ]
    return [_serialize_event_row(row) for row in samples]


def _serialize_event_row(row: dict[str, Any]) -> dict[str, Any]:
    host_id = (row.get("zoom_host_id") or "").strip()
    method = row.get("counseling_method") or CounselingMethod.REMOTE
    zoom_url = (row.get("zoom_url") or "").strip()
    is_remote = method == CounselingMethod.REMOTE

    if not is_remote:
        colors = IN_PERSON_COLORS
    elif host_id:
        colors = _host_colors(host_id)
    else:
        colors = REMOTE_NO_ZOOM_COLORS

    status = row.get("status") or AppointmentStatus.CONFIRMED
    status_label = dict(AppointmentStatus.choices).get(status, status)

    return {
        "id": str(row["id"]),
        "title": row["title"],
        "start": row["start"],
        "end": row["end"],
        "backgroundColor": colors["bg"],
        "borderColor": colors["border"],
        "extendedProps": {
            "client_name": row.get("client_name") or "",
            "counselor_name": row.get("counselor") or row.get("counselor_name") or "",
            "session_number": row.get("session_number"),
            "zoom_host_id": host_id,
            "zoom_host_label": zoom_host_label(host_id) if host_id else "",
            "zoom_url": zoom_url,
            "case_number": row.get("case_number") or "",
            "counseling_method": method,
            "counseling_method_label": "비대면" if is_remote else "대면",
            "status": status,
            "status_label": status_label,
            "is_mock": str(row.get("id", "")).startswith("mock-"),
        },
    }


def build_calendar_events(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    """확정(CONFIRMED) 예약만 FullCalendar 이벤트 JSON으로 변환."""
    query_start, query_end = _db_query_bounds(start, end)
    qs = (
        Appointment.objects.filter(status=AppointmentStatus.CONFIRMED)
        .select_related("client", "counselor", "case", "zoom_meeting")
        .order_by("scheduled_at")
    )
    if query_end is not None:
        qs = qs.filter(scheduled_at__lt=query_end)
    if query_start is not None:
        qs = qs.filter(scheduled_at__gte=query_start)

    appointments: list[Appointment] = []
    intervals: list[CalendarInterval] = []
    for apt in qs:
        try:
            is_remote = apt.case.counseling_method == CounselingMethod.REMOTE
            start_at = _calendar_localtime(apt.scheduled_at)
            duration = apt.duration_minutes or 50
            if duration <= 0:
                duration = 50
            end_at = start_at + timedelta(minutes=duration)
            if not appointment_overlaps_range(
                start_at, end_at, range_start=start, range_end=end
            ):
                continue
            appointments.append(apt)
            intervals.append(
                CalendarInterval(
                    appointment_id=str(apt.id),
                    start=start_at,
                    end=end_at,
                    is_remote=is_remote,
                )
            )
        except Exception:
            logger.exception("캘린더 이벤트 변환 실패 appointment_id=%s", apt.pk)
            continue

    host_assignments = assign_zoom_hosts(intervals)
    events: list[dict[str, Any]] = []

    for apt, interval in zip(appointments, intervals, strict=True):
        try:
            is_remote = interval.is_remote
            zoom_meeting = getattr(apt, "zoom_meeting", None)
            zoom_url = ""
            if zoom_meeting and zoom_meeting.join_url:
                zoom_url = zoom_meeting.join_url.strip()
            elif is_remote and apt.case.zoom_meeting_url:
                zoom_url = apt.case.zoom_meeting_url.strip()

            session_no = apt.session_number
            session_label = f"{session_no}회차" if session_no else "회차 미지정"
            client_name = apt.client.name or "내담자"
            counselor_name = apt.counselor.name or "상담사"

            host_id = host_assignments.get(str(apt.id), "") if is_remote else ""

            row = {
                "id": str(apt.id),
                "title": f"{client_name} ({session_label})",
                "start": interval.start.isoformat(),
                "end": interval.end.isoformat(),
                "counselor": counselor_name,
                "client_name": client_name,
                "session_number": session_no,
                "zoom_host_id": host_id,
                "zoom_url": zoom_url,
                "counseling_method": apt.case.counseling_method,
                "status": apt.status,
                "case_number": apt.case.case_number,
            }
            events.append(_serialize_event_row(row))
        except Exception:
            logger.exception("캘린더 이벤트 직렬화 실패 appointment_id=%s", apt.pk)
            continue

    return events
