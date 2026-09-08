"""Zoom Meeting이 생성된 Account 스냅샷 — 색상·legacy 판별용."""

from __future__ import annotations

from django.conf import settings

ZOOM_ACCOUNT_KIND_LEGACY = "legacy"
ZOOM_ACCOUNT_KIND_CURRENT = "current"


def current_zoom_account_id() -> str:
    return (getattr(settings, "ZOOM_ACCOUNT_ID", None) or "").strip()


def zoom_meeting_account_kind(zoom) -> str:
    """
    비어 있는 zoom_account_id 는 기존 운영 데이터(legacy).
    현재 Railway ZOOM_ACCOUNT_ID 와 같으면 current.
    """
    snapshot = ""
    if zoom is not None:
        snapshot = (getattr(zoom, "zoom_account_id", None) or "").strip()
    current = current_zoom_account_id()
    if snapshot and current and snapshot == current:
        return ZOOM_ACCOUNT_KIND_CURRENT
    return ZOOM_ACCOUNT_KIND_LEGACY
