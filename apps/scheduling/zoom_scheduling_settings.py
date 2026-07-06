"""비대면 Zoom 동시간대 상한 — DB(관리자) + 환경 변수 기본값."""

from __future__ import annotations

from django.conf import settings

from apps.scheduling.zoom_hosts import get_zoom_licensed_user_emails


def get_remote_zoom_simultaneous_capacity() -> int:
    """
    같은 시작 시각에 확정 가능한 비대면 상담 최대 건수.
    관리자 설정 → 환경 변수 기본값(2) 순. Licensed 호스트 수를 넘지 않음.
    """
    from apps.scheduling.models import RemoteZoomSchedulingSettings

    default_cap = int(
        getattr(settings, "DEFAULT_REMOTE_ZOOM_SIMULTANEOUS_CAPACITY", 2) or 2
    )
    try:
        row = RemoteZoomSchedulingSettings.objects.get(
            pk=RemoteZoomSchedulingSettings.SETTINGS_PK
        )
        cap = int(row.simultaneous_session_capacity)
    except RemoteZoomSchedulingSettings.DoesNotExist:
        cap = default_cap

    cap = max(1, cap)
    pool_size = len(get_zoom_licensed_user_emails())
    if pool_size <= 0:
        return cap
    return min(cap, pool_size)


def remote_zoom_host_pool_size() -> int:
    """버퍼 엇갈림 배정에 쓰이는 Licensed Zoom 호스트 수."""
    return len(get_zoom_licensed_user_emails())
