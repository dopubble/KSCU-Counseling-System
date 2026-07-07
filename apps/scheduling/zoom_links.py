"""예약별 Zoom join_url — 대시보드·이메일·캘린더 공통."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from apps.counseling.models import Case
    from apps.scheduling.models import Appointment


class ZoomLaunchPolicyError(RuntimeError):
    """상담사·내담자 UI에 호스트 전용 URL이 노출될 때."""


def appointment_zoom_link_is_locked(appointment: "Appointment") -> bool:
    """
    확정 예약에 join_url·meeting_id가 저장되어 있으면 True.
    자동 Zoom 재생성·URL 일괄 sync 대상에서 제외한다.
    """
    zoom = getattr(appointment, "zoom_meeting", None)
    if zoom is None:
        return False
    join_url = (zoom.join_url or "").strip()
    meeting_id = (zoom.zoom_meeting_id or "").strip()
    return bool(join_url and meeting_id)


def is_zoom_host_url(url: str) -> bool:
    """start_url(/s/)·zak 토큰 URL은 참가 링크로 쓰지 않음."""
    normalized = (url or "").strip().lower()
    if not normalized:
        return False
    return "/s/" in normalized or "zak=" in normalized


def sanitize_participant_zoom_url(url: str) -> str:
    """
    UI·이메일·알림용 참가 URL만 반환.
    호스트 전용 URL이 들어오면 빈 문자열(버튼 비활성) — 로그인 차단 화면 방지.
    """
    normalized = (url or "").strip()
    if not normalized or is_zoom_host_url(normalized):
        return ""
    return normalized


def verify_counselor_zoom_join_policy() -> None:
    """
    배포 전 검사 — 상담사·내담자 resolver가 join_url만 반환하는지 확인.
    start_url 우선 로직이 다시 들어가면 배포를 막는다.
    """
    class _StubZoom:
        join_url = "https://zoom.us/j/81733363550"
        start_url = "https://zoom.us/s/81733363550?zak=secret"

    class _StubApt:
        zoom_meeting = _StubZoom()

    class _StubCase:
        zoom_meeting_url = "https://zoom.us/j/stale"

    apt = _StubApt()
    case = _StubCase()
    join_url = resolve_appointment_zoom_join_url(apt, case)
    counselor_url = resolve_appointment_zoom_counselor_url(apt, case)
    if not join_url or is_zoom_host_url(join_url):
        raise ZoomLaunchPolicyError(
            "resolve_appointment_zoom_join_url이 참가 URL(/j/)을 반환하지 않습니다."
        )
    if counselor_url != join_url:
        raise ZoomLaunchPolicyError(
            "상담사·내담자 Zoom URL이 달라졌습니다. "
            "상담사는 join_url + Claim Host만 사용해야 합니다."
        )
    if is_zoom_host_url(counselor_url):
        raise ZoomLaunchPolicyError(
            "상담사 Zoom URL이 호스트 전용(/s/)입니다. 배포 시 상담 입장 장애가 납니다."
        )


def resolve_appointment_zoom_counselor_url(
    appointment: Optional["Appointment"],
    case: "Case",
) -> str:
    """
    상담사 입장 URL — 기본은 join_url(레거시).

    Zoom API가 저장한 start_url(/s/, zak=)은 호스트 계정 전용이라
    상담사 버튼에 쓰면 「호스트로 로그인」 차단 화면이 뜬다.
    회기별 counselor_host_key 가 있어도 URL 은 join_url 유지(Claim Host).
    """
    return resolve_appointment_zoom_join_url(appointment, case)


def appointment_counselor_host_key(appointment: Optional["Appointment"]) -> str:
    """해당 예약 1건 전용 호스트 키 — ZoomMeeting.counselor_host_key (없으면 빈 문자열)."""
    if appointment is None:
        return ""
    zoom = getattr(appointment, "zoom_meeting", None)
    if zoom is None:
        return ""
    return (getattr(zoom, "counselor_host_key", None) or "").strip()


def resolve_appointment_zoom_join_url(
    appointment: Optional["Appointment"],
    case: "Case",
) -> str:
    """참가 join_url — 내담자·이메일·알림 공통 (호스트 URL 제외)."""
    if appointment is None:
        return ""
    zoom = getattr(appointment, "zoom_meeting", None)
    if zoom and (zoom.join_url or "").strip():
        return sanitize_participant_zoom_url(zoom.join_url)
    case_url = (case.zoom_meeting_url or "").strip()
    return sanitize_participant_zoom_url(case_url)


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
