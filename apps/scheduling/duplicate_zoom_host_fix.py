"""미래 확정 비대면 예약 — 동시간대 동일 zoom_host_email 중복 일괄 재배정."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.counseling.models import CounselingMethod
from apps.reports.appointment_calendar import (
    CalendarInterval,
    _calendar_localtime,
    intervals_conflict_with_buffer,
)
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.utils import (
    ZoomAPIError,
    ZoomNotConfiguredError,
    clear_zoom_token_cache,
    delete_zoom_meeting,
    is_zoom_configured,
)
from apps.scheduling.zoom_hosts import (
    assign_host_emails_for_appointments,
    confirmed_remote_appointments_queryset,
    get_zoom_licensed_user_emails,
    host_id_for_email,
)
from apps.sessions_app.models import ZoomMeeting

KST = ZoneInfo("Asia/Seoul")


def future_confirmed_remote_appointments(
    *,
    scheduled_from: datetime | None = None,
) -> list[Appointment]:
    """오늘 00:00 KST 이후(또는 scheduled_from) 확정 비대면 예약."""
    if scheduled_from is None:
        today = timezone.localtime(timezone.now(), KST).date()
        scheduled_from = datetime.combine(today, datetime.min.time(), tzinfo=KST)

    return list(
        confirmed_remote_appointments_queryset().filter(
            scheduled_at__gte=scheduled_from,
        )
    )


def _stored_host_email(appointment: Appointment) -> str:
    zoom = getattr(appointment, "zoom_meeting", None)
    return (zoom.zoom_host_email or "").strip().lower() if zoom else ""


def _appointment_interval(appointment: Appointment) -> CalendarInterval:
    start = _calendar_localtime(appointment.scheduled_at)
    duration = appointment.duration_minutes or DEFAULT_APPOINTMENT_DURATION_MINUTES
    if duration <= 0:
        duration = DEFAULT_APPOINTMENT_DURATION_MINUTES
    end = start + timedelta(minutes=duration)
    return CalendarInterval(
        appointment_id=str(appointment.pk),
        start=start,
        end=end,
        is_remote=True,
    )


def _intervals_conflict(a: Appointment, b: Appointment) -> bool:
    ia = _appointment_interval(a)
    ib = _appointment_interval(b)
    return intervals_conflict_with_buffer(
        ia.start, ia.end, ib.start, ib.end
    )


def find_same_host_overlap_clusters(
    appointments: list[Appointment],
) -> list[list[Appointment]]:
    """
    30분 버퍼 겹침 + 동일 stored zoom_host_email 클러스터 (2건 이상).
    Union-find로 연결 요소를 묶는다.
    """
    licensed = {
        email.strip().lower()
        for email in get_zoom_licensed_user_emails()
        if email.strip()
    }
    eligible = [
        apt
        for apt in appointments
        if _stored_host_email(apt) in licensed
    ]
    if len(eligible) < 2:
        return []

    parent: dict[int, int] = {apt.pk: apt.pk for apt in eligible}

    def find(pk: int) -> int:
        while parent[pk] != pk:
            parent[pk] = parent[parent[pk]]
            pk = parent[pk]
        return pk

    def union(a: Appointment, b: Appointment) -> None:
        ra, rb = find(a.pk), find(b.pk)
        if ra != rb:
            parent[rb] = ra

    for i, left in enumerate(eligible):
        host = _stored_host_email(left)
        for right in eligible[i + 1 :]:
            if _stored_host_email(right) != host:
                continue
            if _intervals_conflict(left, right):
                union(left, right)

    buckets: dict[int, list[Appointment]] = {}
    for apt in eligible:
        buckets.setdefault(find(apt.pk), []).append(apt)

    clusters = [sorted(group, key=lambda a: (a.scheduled_at, a.pk)) for group in buckets.values()]
    return [group for group in clusters if len(group) > 1]


def collect_duplicate_reassignments(
    appointments: list[Appointment],
    *,
    include_all_mismatches: bool = False,
) -> list[tuple[Appointment, str, str]]:
    """
    재배정 대상 (appointment, stored_email, target_email).
    기본: 동일 호스트 중복 클러스터 내 stored ≠ 알고리즘 기대값.
    include_all_mismatches=True: 미래 전체 중 stored ≠ 기대값.
    """
    licensed = {
        email.strip().lower()
        for email in get_zoom_licensed_user_emails()
        if email.strip()
    }
    expected = assign_host_emails_for_appointments(appointments)

    duplicate_pks: set[int] = set()
    clusters = find_same_host_overlap_clusters(appointments)
    for group in clusters:
        for apt in group:
            duplicate_pks.add(apt.pk)

    reassignments: list[tuple[Appointment, str, str]] = []
    for apt in appointments:
        stored = _stored_host_email(apt)
        exp = (expected.get(str(apt.pk), "") or "").strip().lower()
        if not exp:
            continue
        if stored and stored not in licensed:
            continue
        if stored == exp:
            continue
        if apt.pk not in duplicate_pks and not include_all_mismatches:
            continue

        zoom = getattr(apt, "zoom_meeting", None)
        meeting_id = (zoom.zoom_meeting_id or "").strip() if zoom else ""
        if not meeting_id:
            continue

        reassignments.append((apt, stored, exp))

    licensed_list = get_zoom_licensed_user_emails()
    primary = licensed_list[0].strip().lower() if licensed_list else ""

    reassignments.sort(
        key=lambda item: (
            item[2] == primary,
            item[0].scheduled_at,
            str(item[0].pk),
        )
    )
    return reassignments


def _is_zoom_daily_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "429" in msg
        or "rate limit" in msg
        or "too many" in msg
        or "400 meeting create" in msg
        or "400 meeting update" in msg
    )


def reassign_appointment_zoom_host(
    appointment: Appointment,
    host_user_email: str,
    *,
    dry_run: bool = False,
    notify_link_change: bool = False,
) -> str:
    """locked 무시 — Zoom 회의 재생성. 반환: 결과 메시지."""
    target = (host_user_email or "").strip()
    if not target:
        raise ValueError("host_user_email 비어 있음")

    zoom = getattr(appointment, "zoom_meeting", None)
    stored = (zoom.zoom_host_email or "").strip().lower() if zoom else ""
    label = (
        f"{appointment.client.name} "
        f"{timezone.localtime(appointment.scheduled_at):%Y-%m-%d %H:%M}"
    )
    if stored == target.lower():
        return f"[skip] {label}: 이미 {host_id_for_email(target)}"

    if dry_run:
        old = stored or "(empty)"
        return (
            f"[would fix] {label}: {host_id_for_email(old) or old} "
            f"-> {host_id_for_email(target)} ({target})"
        )

    from apps.scheduling.services import _create_zoom_meeting_for_appointment

    old_meeting_id = (zoom.zoom_meeting_id or "").strip() if zoom else ""
    _create_zoom_meeting_for_appointment(
        appointment,
        host_user_email=target,
        notify_link_change=notify_link_change,
    )
    refreshed = ZoomMeeting.objects.filter(appointment_id=appointment.pk).first()
    new_id = (refreshed.zoom_meeting_id or "").strip() if refreshed else ""
    if old_meeting_id and new_id and old_meeting_id != new_id:
        delete_zoom_meeting(old_meeting_id)

    return f"[fixed] {label}: -> {host_id_for_email(target)} ({target})"


def fix_duplicate_future_zoom_hosts(
    *,
    dry_run: bool = True,
    scheduled_from: datetime | None = None,
    include_all_mismatches: bool = False,
    notify_link_change: bool = False,
    limit: int | None = None,
    stop_on_rate_limit: bool = True,
) -> tuple[int, int, int, list[str], list[list[Appointment]]]:
    """
    미래 확정 비대면 중복 호스트 일괄 재배정.
    반환: (fixed, skipped, clusters_found, messages, clusters)
    """
    if not is_zoom_configured():
        raise ZoomNotConfiguredError(
            "Zoom API 설정이 없습니다. ZOOM_* 환경 변수를 확인해 주세요."
        )

    appointments = future_confirmed_remote_appointments(
        scheduled_from=scheduled_from,
    )
    clusters = find_same_host_overlap_clusters(appointments)
    reassignments = collect_duplicate_reassignments(
        appointments,
        include_all_mismatches=include_all_mismatches,
    )

    if limit is not None and limit > 0:
        reassignments = reassignments[:limit]

    fixed = skipped = 0
    messages: list[str] = []

    if dry_run:
        for group in clusters:
            names = ", ".join(a.client.name for a in group)
            host = host_id_for_email(_stored_host_email(group[0]))
            when = timezone.localtime(group[0].scheduled_at).strftime("%Y-%m-%d %H:%M")
            messages.append(
                f"[cluster] {when} {host}: {names} ({len(group)}건)"
            )
        for apt, stored, exp in reassignments:
            msg = reassign_appointment_zoom_host(
                apt, exp, dry_run=True, notify_link_change=False
            )
            messages.append(msg)
            fixed += 1
        skipped = max(0, len(appointments) - len(reassignments))
        return fixed, skipped, len(clusters), messages, clusters

    for apt, _stored, exp in reassignments:
        try:
            msg = reassign_appointment_zoom_host(
                apt,
                exp,
                dry_run=False,
                notify_link_change=notify_link_change,
            )
            messages.append(msg)
            fixed += 1
        except (ZoomAPIError, ZoomNotConfiguredError) as exc:
            clear_zoom_token_cache()
            label = (
                f"{apt.client.name} "
                f"{timezone.localtime(apt.scheduled_at):%Y-%m-%d %H:%M}"
            )
            if stop_on_rate_limit and _is_zoom_daily_quota_error(exc):
                messages.append(f"[rate limit] {label}: {exc}")
                skipped += len(reassignments) - fixed
                break
            messages.append(f"[error] {label}: {exc}")
            skipped += 1

    return fixed, skipped, len(clusters), messages, clusters
