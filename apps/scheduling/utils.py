"""
Zoom Server-to-Server OAuth API (requests)

.env: ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

TOKEN_URL = "https://zoom.us/oauth/token"
API_BASE = "https://api.zoom.us/v2"

_token_cache: dict[str, Any] = {"access_token": None, "expires_at": None}


class ZoomAPIError(Exception):
    """Zoom API 호출 실패"""


class ZoomNotConfiguredError(ZoomAPIError):
    """환경 변수 미설정"""


def _zoom_credentials() -> tuple[str, str, str]:
    account_id = (settings.ZOOM_ACCOUNT_ID or "").strip()
    client_id = (settings.ZOOM_CLIENT_ID or "").strip()
    client_secret = (settings.ZOOM_CLIENT_SECRET or "").strip()
    return account_id, client_id, client_secret


def is_zoom_configured() -> bool:
    account_id, client_id, client_secret = _zoom_credentials()
    return bool(account_id and client_id and client_secret)


def _ensure_zoom_configured() -> tuple[str, str, str]:
    account_id, client_id, client_secret = _zoom_credentials()
    if not (account_id and client_id and client_secret):
        raise ZoomNotConfiguredError(
            "Zoom API 설정이 없습니다. .env에 ZOOM_ACCOUNT_ID, "
            "ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET을 입력해 주세요."
        )
    return account_id, client_id, client_secret


def get_zoom_access_token() -> str:
    """Server-to-Server OAuth 액세스 토큰 발급"""
    account_id, client_id, client_secret = _ensure_zoom_configured()
    now = timezone.now()
    cached = _token_cache.get("access_token")
    expires_at = _token_cache.get("expires_at")
    if cached and expires_at and now < expires_at:
        return cached

    try:
        response = requests.post(
            TOKEN_URL,
            params={
                "grant_type": "account_credentials",
                "account_id": account_id,
            },
            auth=(client_id, client_secret),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.Timeout as exc:
        raise ZoomAPIError(
            "Zoom 인증 서버 응답 시간이 초과되었습니다. 네트워크 연결을 확인해 주세요."
        ) from exc
    except requests.ConnectionError as exc:
        raise ZoomAPIError(
            "Zoom 서버에 연결할 수 없습니다. 인터넷 연결을 확인해 주세요."
        ) from exc
    except requests.HTTPError as exc:
        logger.exception(
            "Zoom OAuth token error: %s",
            exc.response.text if exc.response is not None else exc,
        )
        raise ZoomAPIError(
            "Zoom API 인증에 실패했습니다. Client ID·Secret·Account ID를 확인해 주세요."
        ) from exc
    except requests.RequestException as exc:
        raise ZoomAPIError(f"Zoom 인증 요청 중 오류가 발생했습니다: {exc}") from exc

    token = data.get("access_token")
    if not token:
        raise ZoomAPIError("Zoom API에서 액세스 토큰을 받지 못했습니다.")

    expires_in = int(data.get("expires_in", 3600))
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = now + timedelta(seconds=max(expires_in - 60, 60))
    return token


def clear_zoom_token_cache() -> None:
    """Scope 변경·인증 오류 후 재발급을 위해 프로세스 내 토큰 캐시 초기화."""
    _token_cache["access_token"] = None
    _token_cache["expires_at"] = None


def counselor_zoom_host_email(counselor) -> str:
    """상담사 대체 호스트 등록용 Zoom 이메일 (플랫폼 계정 이메일)."""
    if counselor is None:
        return ""
    return (getattr(counselor, "email", None) or "").strip()


def _zoom_meeting_settings(*, alternative_host_email: str | None = None) -> dict[str, Any]:
    settings_payload: dict[str, Any] = {
        "join_before_host": True,
        "waiting_room": False,
        "host_video": True,
        "participant_video": True,
    }
    host_email = (alternative_host_email or "").strip()
    if host_email:
        settings_payload["alternative_hosts"] = host_email
        settings_payload["alternative_hosts_email_notification"] = False
    return settings_payload


def create_zoom_meeting(
    *,
    topic: str,
    start_time: datetime,
    duration_minutes: int,
    timezone_name: str | None = None,
    alternative_host_email: str | None = None,
) -> dict[str, Any]:
    """
    Zoom 예약 회의 생성.
    반환: id, join_url, start_url, password 등 API JSON

    alternative_host_email: 상담사 개인 Zoom(동일 이메일)으로 대체 호스트 입장.
    """
    _ensure_zoom_configured()
    tz = timezone_name or settings.TIME_ZONE
    if timezone.is_naive(start_time):
        start_time = timezone.make_aware(start_time, timezone.get_current_timezone())

    local_start = timezone.localtime(start_time, timezone.get_current_timezone())
    payload = {
        "topic": topic[:200],
        "type": 2,
        "start_time": local_start.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration": duration_minutes,
        "timezone": tz,
        "settings": _zoom_meeting_settings(
            alternative_host_email=alternative_host_email,
        ),
    }

    token = get_zoom_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            f"{API_BASE}/users/me/meetings",
            json=payload,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except requests.Timeout as exc:
        raise ZoomAPIError(
            "Zoom 회의 생성 요청 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
        ) from exc
    except requests.ConnectionError as exc:
        raise ZoomAPIError(
            "Zoom 서버에 연결할 수 없습니다. 인터넷 연결을 확인해 주세요."
        ) from exc
    except requests.HTTPError as exc:
        detail = ""
        if exc.response is not None:
            try:
                detail = exc.response.json().get("message", exc.response.text)
            except Exception:
                detail = exc.response.text
        logger.exception("Zoom create meeting error: %s", detail)
        raise ZoomAPIError(
            f"Zoom 회의 생성에 실패했습니다. {detail or 'API 오류'}"
        ) from exc
    except requests.RequestException as exc:
        raise ZoomAPIError(f"Zoom 회의 생성 중 오류가 발생했습니다: {exc}") from exc


def _patch_zoom_meeting(meeting_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_zoom_configured()
    meeting_id = str(meeting_id).strip()
    if not meeting_id:
        raise ZoomAPIError("Zoom 회의 ID가 없습니다.")

    token = get_zoom_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.patch(
            f"{API_BASE}/meetings/{meeting_id}",
            json=payload,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}
    except requests.Timeout as exc:
        raise ZoomAPIError(
            "Zoom 회의 변경 요청 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
        ) from exc
    except requests.ConnectionError as exc:
        raise ZoomAPIError(
            "Zoom 서버에 연결할 수 없습니다. 인터넷 연결을 확인해 주세요."
        ) from exc
    except requests.HTTPError as exc:
        detail = ""
        if exc.response is not None:
            try:
                detail = exc.response.json().get("message", exc.response.text)
            except Exception:
                detail = exc.response.text
        logger.exception("Zoom update meeting error: %s", detail)
        raise ZoomAPIError(
            f"Zoom 회의 변경에 실패했습니다. {detail or 'API 오류'}"
        ) from exc
    except requests.RequestException as exc:
        raise ZoomAPIError(f"Zoom 회의 변경 중 오류가 발생했습니다: {exc}") from exc


def update_zoom_meeting(
    meeting_id: str,
    *,
    start_time: datetime,
    duration_minutes: int,
    timezone_name: str | None = None,
    alternative_host_email: str | None = None,
) -> dict[str, Any]:
    """Zoom 예약 회의 일시·시간·대체 호스트 변경."""
    tz = timezone_name or settings.TIME_ZONE
    if timezone.is_naive(start_time):
        start_time = timezone.make_aware(start_time, timezone.get_current_timezone())

    local_start = timezone.localtime(start_time, timezone.get_current_timezone())
    payload: dict[str, Any] = {
        "start_time": local_start.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration": duration_minutes,
        "timezone": tz,
    }
    host_email = (alternative_host_email or "").strip()
    if host_email:
        payload["settings"] = _zoom_meeting_settings(
            alternative_host_email=host_email,
        )
    return _patch_zoom_meeting(meeting_id, payload)


def update_zoom_meeting_alternative_host(
    meeting_id: str,
    alternative_host_email: str,
) -> dict[str, Any]:
    """기존 회의에 상담사 대체 호스트만 등록."""
    host_email = (alternative_host_email or "").strip()
    if not host_email:
        raise ZoomAPIError("대체 호스트 이메일이 없습니다.")
    return _patch_zoom_meeting(
        meeting_id,
        {"settings": _zoom_meeting_settings(alternative_host_email=host_email)},
    )


def pick_meeting_launch_url(meeting_data: dict[str, Any]) -> str:
    """참가 URL — 상담사·내담자 공통 (개인 Zoom 계정 입장)."""
    return (meeting_data.get("join_url") or meeting_data.get("start_url") or "").strip()
