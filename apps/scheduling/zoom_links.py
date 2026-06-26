"""예약별 Zoom join_url — 대시보드·이메일·캘린더 공통."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from apps.counseling.models import Case
    from apps.scheduling.models import Appointment


def is_zoom_host_url(url: str) -> bool:
    """start_url(/s/)·zak 토큰 URL은 참가 링크로 쓰지 않음."""
    normalized = (url or "").strip().lower()
    if not normalized:
        return False
    return "/s/" in normalized or "zak=" in normalized


def resolve_appointment_zoom_join_url(
    appointment: Optional["Appointment"],
    case: "Case",
) -> str:
    """참가 join_url — 상담사·내담자·이메일 공통."""
    if appointment is None:
        return ""
    zoom = getattr(appointment, "zoom_meeting", None)
    if zoom and (zoom.join_url or "").strip():
        return zoom.join_url.strip()
    case_url = (case.zoom_meeting_url or "").strip()
    if case_url and not is_zoom_host_url(case_url):
        return case_url
    return ""


def capture_appointment_zoom_join_url(appointment: "Appointment") -> str:
    """Zoom 변경 전 현재 링크 (알림 비교용)."""
    case = appointment.case
    return resolve_appointment_zoom_join_url(appointment, case)


def sync_case_zoom_meeting_url(
    appointment: "Appointment",
    *,
    join_url: str | None = None,
) -> str:
    """Case.zoom_meeting_url을 해당 예약 join_url과 맞춤. 반환: 동기화된 URL."""
    case = appointment.case
    if join_url is None:
        join_url = resolve_appointment_zoom_join_url(appointment, case)
    else:
        join_url = join_url.strip()
    if join_url and case.zoom_meeting_url != join_url:
        case.zoom_meeting_url = join_url
        case.save(update_fields=["zoom_meeting_url"])
    return join_url
