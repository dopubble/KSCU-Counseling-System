"""로그인/회원가입 후 안전한 리다이렉트(next) 처리."""

from __future__ import annotations

from functools import wraps
from urllib.parse import urlparse

from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.models import UserRole


def skip_session_save(view_func):
    """
    폴링 등 읽기 전용 API에서 세션 저장을 건너뜁니다.
    동시 요청 시 DB 세션 락 경합을 줄입니다.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        finally:
            request.session.modified = False

    return wrapper


def get_safe_next_url(request, *, default: str | None = None) -> str | None:
    """GET/POST의 next가 허용된 호스트일 때만 반환."""
    candidate = request.POST.get("next") or request.GET.get("next")
    if not candidate:
        return default
    if url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return default


def default_dashboard_url_for_user(user) -> str:
    """역할별 기본 대시보드."""
    if user.is_superuser or user.role == UserRole.ADMIN:
        return reverse("admin_panel:dashboard")
    if user.role == UserRole.COUNSELOR:
        return reverse("counselor:dashboard")
    if user.role == UserRole.SUPERVISOR:
        return reverse("supervisor:dashboard")
    if user.role == UserRole.CLIENT:
        return reverse("client:dashboard")
    return reverse("home")


def url_path(candidate_url: str) -> str:
    if candidate_url.startswith("/"):
        return candidate_url.split("?", 1)[0]
    return urlparse(candidate_url).path.split("?", 1)[0] or "/"


def user_can_access_url_path(user, path: str) -> bool:
    """로그인 후 next URL 진입이 역할상 허용되는지."""
    if not path or path == "/":
        return True

    public_prefixes = (
        "/accounts/profile",
        "/accounts/logout",
        "/accounts/pending",
        "/manual/",
        "/counseling/apply",
    )
    for prefix in public_prefixes:
        if path.startswith(prefix):
            return True

    if user.is_superuser:
        return True

    role = user.role

    if path.startswith("/admin-panel/") or path.startswith("/admin/"):
        return role == UserRole.ADMIN

    if path.startswith("/counseling/supervisor/"):
        return role in (UserRole.SUPERVISOR, UserRole.ADMIN)

    if path.startswith("/counseling/counselor/"):
        return role in (UserRole.COUNSELOR, UserRole.ADMIN)

    if path.startswith("/client/"):
        return role == UserRole.CLIENT

    if path.startswith(("/scheduling/", "/documents/")):
        return role in (UserRole.COUNSELOR, UserRole.CLIENT, UserRole.ADMIN)

    if path.startswith("/sessions/"):
        return role in (UserRole.COUNSELOR, UserRole.ADMIN)

    if path.startswith("/counseling/"):
        return role in (
            UserRole.COUNSELOR,
            UserRole.CLIENT,
            UserRole.ADMIN,
            UserRole.SUPERVISOR,
        )

    return False


def resolve_post_login_redirect(
    request,
    user,
    *,
    candidate_url: str | None = None,
) -> str:
    """
    로그인 성공 후 리다이렉트 URL.
    next가 호스트·역할 모두 허용될 때만 사용하고, 아니면 역할별 기본 대시보드.
    """
    raw = (candidate_url or "").strip()
    if raw and url_has_allowed_host_and_scheme(
        url=raw,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        if user_can_access_url_path(user, url_path(raw)):
            return raw
        fallback = default_dashboard_url_for_user(user)
        from apps.accounts.access_diagnostics import log_login_next_rejected

        log_login_next_rejected(
            request,
            user,
            candidate_url=raw,
            resolved_url=fallback,
        )
        return fallback
    return default_dashboard_url_for_user(user)
