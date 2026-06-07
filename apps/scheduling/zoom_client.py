"""하위 호환 — Zoom API는 apps.scheduling.utils 를 사용하세요."""

from .utils import (  # noqa: F401
    ZoomAPIError,
    ZoomNotConfiguredError,
    create_zoom_meeting,
    get_zoom_access_token,
    is_zoom_configured,
)


class ZoomClient:
    """레거시 래퍼 — utils 함수 호출"""

    def is_configured(self) -> bool:
        return is_zoom_configured()

    def get_access_token(self) -> str:
        return get_zoom_access_token()

    def create_meeting(self, **kwargs):
        return create_zoom_meeting(**kwargs)
