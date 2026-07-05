"""접근 거부(403) 및 로그인 리다이렉트 진단 로그."""

from __future__ import annotations

import logging

logger = logging.getLogger("apps.accounts.access")


def _next_params(request) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in ("next", "redirect", "redirect_to"):
        raw = request.GET.get(key) or request.POST.get(key)
        if raw:
            values[key] = raw.strip()
    return values


def _user_snapshot(user) -> dict:
    if not getattr(user, "is_authenticated", False):
        return {"authenticated": False}
    return {
        "authenticated": True,
        "user_id": str(user.pk),
        "email": user.email,
        "role": getattr(user, "role", ""),
        "status": getattr(user, "status", ""),
        "is_superuser": bool(getattr(user, "is_superuser", False)),
    }


def log_permission_denied(request, *, exception=None) -> None:
    """403 handler403 진입 시 요청·next·사용자 정보를 WARNING으로 기록."""
    next_params = _next_params(request)
    logger.warning(
        "HTTP 403 permission denied path=%s method=%s full_path=%s referer=%s "
        "next_params=%s user=%s exception=%s",
        request.path,
        request.method,
        request.get_full_path(),
        request.META.get("HTTP_REFERER", ""),
        next_params or None,
        _user_snapshot(request.user),
        str(exception) if exception else "",
    )


def log_login_next_rejected(
    request,
    user,
    *,
    candidate_url: str,
    resolved_url: str,
) -> None:
    """로그인 후 next URL이 역할 불일치로 거부·대체 리다이렉트될 때 INFO로 기록."""
    logger.info(
        "login next rejected by role path=%s candidate_next=%s resolved_url=%s user=%s",
        url_path_for_log(candidate_url),
        candidate_url,
        resolved_url,
        _user_snapshot(user),
    )


def url_path_for_log(candidate_url: str) -> str:
    from apps.accounts.auth_utils import url_path

    return url_path(candidate_url)
